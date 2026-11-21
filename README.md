# vm-platform

Minimal Ansible playbooks (`community.proxmox`) to initialize a single-node or multi-node Proxmox VE cluster once and provision isolated, internal, or LAN VMs on `local-lvm`.

## Network profiles

| Profile | Default Network | Bridge | Rules |
|---|---|---|---|
| `isolated` | `10.100.10.0/24` | `vpiso` (VXLAN 1002) | Denies all new connections to/from other VMs and LAN; SSH via Bastion, DNS via Gateway |
| `internal` | `10.100.1.0/24` | `vpint` (VXLAN 1001) | Allows peers in `internal` only (same node or across cluster nodes); blocks `isolated`, `lan`, LAN; SSH via Bastion, DNS via Gateway |
| `lan` | Your LAN CIDR | `vmbr0` | Allows `lan` VMs and LAN devices; blocks protected infrastructure, `isolated`, and `internal` |

All profiles block access to protected management/infrastructure IPs (router, all Proxmox nodes, Gateway, Bastion) and block IPv6/DHCP spoofing at the PVE Firewall + host bridge guard layer.

## Quick start

### 1. Copy and edit your local inventory (1 file, git-ignored)

```bash
cp ansible/inventory.example.yml ansible/inventory.yml
```

Fill in your Proxmox API host, LAN subnet/gateway, two unused static LAN IPs for Gateway & Bastion, SSH key path, and Proxmox node(s) under `proxmox_nodes`.

Export your Proxmox API token:

```bash
export VP_API_TOKEN_ID="automation"
export VP_API_TOKEN_SECRET="your-token-secret"
```

Download the cloud image(s) you plan to use into `/var/lib/vz/import/` on your Proxmox node(s) (matching `vmp_os_catalog` in `ansible/roles/defaults/defaults/main.yml` or overridden in `inventory.yml`), and make sure Proxmox Datacenter Firewall is enabled.

### 2. Initialize the platform once (`init.yml`)

Sets up private VXLAN bridges (`vpint`, `vpiso`) and host bridge guard on every node in `proxmox_nodes`, configures Proxmox cluster IPSets/Security Groups, and deploys one shared Debian Gateway (`100`) and OpenSSH Bastion (`101`) for the cluster:

```bash
cd ansible
ansible-playbook -i inventory.yml playbooks/init.yml
```

Add `--check` to dry-run without modifying Proxmox.

### 3. Create a VM (`create.yml`)

Copy `ansible/vm.example.yml` to `ansible/vm-app01.yml` (git-ignored):

```yaml
vm:
  name: app01
  profile: isolated          # isolated | internal | lan
  os: debian-13              # see vmp_os_catalog
  cores: 2
  memory_mib: 2048
  disk_gib: 20
  ssh_user: appadmin
  ssh_public_key: "ssh-ed25519 AAAA..."
  # node: pve02              # optional: target cluster node (defaults to first node)
```

Run:

```bash
cd ansible
ansible-playbook -i inventory.yml playbooks/create.yml -e @vm-app01.yml
```

- Add `--check` to preview auto-picked VMID/IP/MAC without creating anything.
- `create.yml` imports the cloud image onto `local-lvm`, applies PVE Firewall **before boot**, and adds only this VM's drop-in config on Gateway & Bastion without stopping Gateway, Bastion, or other VMs.

### 4. Delete a VM (`delete.yml`)

Removes only that VM's Bastion user/config, Gateway drop-in, cluster IPSet entry, and Proxmox VM + disk (zero downtime for Gateway, Bastion, and other VMs; refuses VMID `100`/`101`):

```bash
cd ansible
ansible-playbook -i inventory.yml playbooks/delete.yml -e vmid=102
```

## Adding another Proxmox node in the same cluster

1. Join the new node (e.g. `pve02`) to your Proxmox cluster on the same LAN and copy your cloud `.qcow2` image(s) into `/var/lib/vz/import/` on `pve02`.
2. Add `pve02` under `proxmox_nodes:` in your `ansible/inventory.yml`.
3. Re-run `ansible-playbook -i inventory.yml playbooks/init.yml` — it automatically configures `vxlan-remoteip` between all nodes, adds `pve02` to `vmp_protected`, and reuses the existing cluster Gateway & Bastion without recreating them.
4. Set `node: pve02` in your `vm.yml` when running `create.yml`.

## Run via Docker (optional)

```bash
docker compose --env-file /dev/null run --rm tools
docker compose --env-file /dev/null run --rm controller ansible-playbook -i /input/inventory.yml playbooks/init.yml
```
