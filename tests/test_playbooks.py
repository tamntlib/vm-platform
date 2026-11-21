"""Offline verification for vm-platform playbooks, templates, multi-node VXLAN, optional Edge, and public hygiene."""
from pathlib import Path
import subprocess
import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = ROOT / "ansible"
TEMPLATES = ANSIBLE / "playbooks" / "templates"
DEFAULTS = ANSIBLE / "roles" / "defaults" / "defaults" / "main.yml"


def load_vars():
    raw = yaml.safe_load(DEFAULTS.read_text())
    raw["vmp_lan_prefixlen"] = 24
    raw["vmp_internal_edge"] = raw["vmp_internal_pool_prefix"] + "3"
    raw["vmp_isolated_edge"] = raw["vmp_isolated_pool_prefix"] + "3"
    raw["vmp_networks"]["internal"].update({
        "vni": raw["vmp_internal_vni"],
        "cidr": raw["vmp_internal_cidr"],
        "prefixlen": 24,
        "gateway": raw["vmp_internal_gateway"],
        "bastion": raw["vmp_internal_bastion"],
        "edge": raw["vmp_internal_edge"],
    })
    raw["vmp_networks"]["isolated"].update({
        "vni": raw["vmp_isolated_vni"],
        "cidr": raw["vmp_isolated_cidr"],
        "prefixlen": 24,
        "gateway": raw["vmp_isolated_gateway"],
        "bastion": raw["vmp_isolated_bastion"],
        "edge": raw["vmp_isolated_edge"],
    })
    raw["vmp_networks"]["lan"].update({
        "bridge": raw["vmp_lan_bridge"],
        "mtu": raw["vmp_lan_mtu"],
        "cidr": raw["vmp_lan_cidr"],
        "prefixlen": 24,
        "gateway": raw["vmp_lan_gateway"],
        "bastion": raw["vmp_bastion_lan_ip"],
        "edge": raw["vmp_edge_lan_ip"],
    })
    raw["vmp_gateway"]["lan_ip"] = raw["vmp_gateway_lan_ip"]
    raw["vmp_gateway"]["bastion_alias_ip"] = raw["vmp_bastion_lan_ip"]
    raw["vmp_bastion"]["internal_ip"] = raw["vmp_internal_bastion"]
    raw["vmp_bastion"]["isolated_ip"] = raw["vmp_isolated_bastion"]
    raw["vmp_edge"]["lan_alias_ip"] = raw["vmp_edge_lan_ip"]
    raw["vmp_edge"]["internal_ip"] = raw["vmp_internal_edge"]
    raw["vmp_edge"]["isolated_ip"] = raw["vmp_isolated_edge"]
    raw["vmp_edge_effective_allowed_cidrs"] = ["0.0.0.0/0"]
    raw["vmp_edge_effective_trusted_proxies"] = []
    return raw


def render(template_name, extra=None):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), keep_trailing_newline=True)
    env.filters["bool"] = lambda v: v if isinstance(v, bool) else str(v).strip().lower() in ("true", "1", "yes", "on")
    ctx = load_vars()
    if extra:
        ctx.update(extra)
    return env.get_template(template_name).render(**ctx)


def test_playbook_syntax():
    for pb in ("playbooks/init.yml", "playbooks/create.yml", "playbooks/publish.yml", "playbooks/delete.yml"):
        res = subprocess.run(
            ["ansible-playbook", "-i", "inventory.example.yml", "--syntax-check", pb],
            cwd=str(ANSIBLE),
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"{pb} syntax failed:\n{res.stdout}\n{res.stderr}"


def test_private_networks_single_and_multi_node():
    single = render("private-networks.j2", {
        "inventory_hostname": "pve01",
        "ansible_host": "192.168.100.10",
        "groups": {"proxmox_nodes": ["pve01"]},
        "hostvars": {"pve01": {"ansible_host": "192.168.100.10"}},
    })
    assert "auto vpint" in single
    assert "auto vpiso" in single
    assert "vxlan-local-tunnelip 192.168.100.10" in single
    assert "vxlan-learning no" in single
    assert "vxlan-remoteip" not in single
    assert "vmbr0" not in single

    multi = render("private-networks.j2", {
        "inventory_hostname": "pve01",
        "ansible_host": "192.168.100.10",
        "groups": {"proxmox_nodes": ["pve01", "pve02", "pve03"]},
        "hostvars": {
            "pve01": {"ansible_host": "192.168.100.10"},
            "pve02": {"ansible_host": "192.168.100.11"},
            "pve03": {"ansible_host": "192.168.100.12"},
        },
    })
    assert "vxlan-learning yes" in multi
    assert "vxlan-remoteip 192.168.100.11" in multi
    assert "vxlan-remoteip 192.168.100.12" in multi
    assert "vxlan-remoteip 192.168.100.10" not in multi


def test_host_guard_template_scopes_only_private_bridges():
    rendered = render("host-guard.nft.j2")
    assert "table bridge vmp_guard" in rendered
    assert '"vpint", "vpiso"' in rendered
    assert "ether type != { ip, arp } drop" in rendered
    assert "vmbr0" not in rendered


def test_gateway_nftables_and_dnsmasq_templates():
    base_args = {
        "gw_lan_iface": "ens18",
        "gw_int_iface": "ens19",
        "gw_iso_iface": "ens20",
    }
    nft = render("gateway-nftables.conf.j2", base_args)
    assert "table inet vmp_gw" in nft
    assert "set dns_internal" in nft
    assert "set dns_isolated" in nft
    assert "set lan_ssh_targets" in nft
    assert "dnat ip to 10.100.1.2:2222" in nft
    assert "edge_sources" not in nft
    assert "10.100.1.3" not in nft
    assert 'include "/etc/vm-platform-gateway/clients.d/*.nft"' in nft

    # Optional Edge enabled with generic public source (no Cloudflare required)
    nft_public = render("gateway-nftables.conf.j2", {
        **base_args,
        "vmp_edge_enabled": True,
        "vmp_edge_source_preset": "public",
    })
    assert "set edge_sources" not in nft_public
    assert "ip daddr 192.168.100.252 tcp dport { 80, 443 } dnat ip to 10.100.1.3" in nft_public
    assert "ip saddr @edge_sources" not in nft_public
    assert "ip saddr 10.100.1.3 snat ip to 192.168.100.250" in nft_public

    # Optional Edge enabled with Cloudflare preset
    cfg = load_vars()
    nft_cf = render("gateway-nftables.conf.j2", {
        **base_args,
        "vmp_edge_enabled": True,
        "vmp_edge_source_preset": "cloudflare",
        "vmp_edge_effective_allowed_cidrs": cfg["vmp_edge_cloudflare_ipv4_cidrs"],
    })
    assert "set edge_sources {" in nft_cf
    assert "flags interval;" in nft_cf
    assert "173.245.48.0/20" in nft_cf
    assert "ip saddr @edge_sources ip daddr 192.168.100.252 tcp dport { 80, 443 } dnat ip to 10.100.1.3" in nft_cf

    dns = render("gateway-dnsmasq.conf.j2")
    assert "listen-address=10.100.1.1" in dns
    assert "listen-address=10.100.10.1" in dns
    assert "no-dhcp-interface=*" in dns


def test_edge_caddyfile_public_and_cloudflare_modes():
    public_caddy = render("edge-caddyfile.j2")
    assert "(vmp_proxy_opts)" in public_caddy
    assert "trusted_proxies" not in public_caddy
    assert "import /etc/caddy/sites.d/*.caddy" in public_caddy

    cfg = load_vars()
    cf_caddy = render("edge-caddyfile.j2", {
        "vmp_edge_acme_email": "ops@example.com",
        "vmp_edge_effective_trusted_proxies": cfg["vmp_edge_cloudflare_ipv4_cidrs"],
    })
    assert "email ops@example.com" in cf_caddy
    assert "trusted_proxies 173.245.48.0/20" in cf_caddy
    assert "import /etc/caddy/sites.d/*.caddy" in cf_caddy


def test_bastion_sshd_template_locks_down_jump_users():
    conf = render("bastion-sshd.conf.j2")
    assert "Port 2222" in conf
    assert "AllowGroups vmpjump" in conf
    assert "PermitRootLogin no" in conf
    assert "PasswordAuthentication no" in conf
    assert "AllowAgentForwarding no" in conf
    assert "PermitTTY no" in conf
    assert "Include /etc/vm-platform-bastion/users.d/*.conf" in conf


def test_storage_defaults_use_local_lvm_and_neutral_lan():
    cfg = load_vars()
    assert cfg["vmp_vm_storage"] == "local-lvm"
    assert cfg["vmp_image_storage"] == "local"
    assert cfg["vmp_vmid_min"] == 103
    assert cfg["vmp_edge_enabled"] is False
    assert cfg["vmp_edge_source_preset"] == "public"
    assert cfg["vmp_lan_cidr"] == "192.168.100.0/24"
    assert cfg["vmp_networks"]["internal"]["cidr"] == "10.100.1.0/24"
    assert cfg["vmp_networks"]["isolated"]["cidr"] == "10.100.10.0/24"


def test_inventory_overrides_role_defaults(tmp_path):
    inv = tmp_path / "inventory.yml"
    inv.write_text(
        "all:\n"
        "  vars:\n"
        "    vmp_lan_cidr: 10.99.0.0/23\n"
        "    vmp_gateway_lan_ip: 10.99.0.250\n"
        "    vmp_edge_enabled: true\n"
        "    vmp_edge_lan_ip: 10.99.0.252\n"
        "    vmp_edge_source_preset: cloudflare\n"
        "    vmp_internal_vni: 2001\n"
        "    vmp_internal_pool_prefix: '10.88.1.'\n"
        "    vmp_isolated_pool_prefix: '10.88.10.'\n"
        "  children:\n"
        "    proxmox_nodes:\n"
        "      hosts:\n"
        "        pve1:\n"
        "          ansible_host: 10.99.0.10\n"
    )
    pb = tmp_path / "check_override.yml"
    pb.write_text(
        "- hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: false\n"
        "  roles:\n"
        "    - defaults\n"
        "  tasks:\n"
        "    - ansible.builtin.assert:\n"
        "        that:\n"
        "          - vmp_lan_cidr == '10.99.0.0/23'\n"
        "          - (vmp_networks.lan.prefixlen | int) == 23\n"
        "          - vmp_gateway.lan_ip == '10.99.0.250'\n"
        "          - vmp_edge_enabled | bool\n"
        "          - vmp_edge.lan_alias_ip == '10.99.0.252'\n"
        "          - vmp_edge.internal_ip == '10.88.1.3'\n"
        "          - vmp_edge.isolated_ip == '10.88.10.3'\n"
        "          - (vmp_edge_effective_allowed_cidrs | length) == 15\n"
        "          - (vmp_edge_effective_trusted_proxies | length) == 15\n"
        "          - (vmp_networks.internal.vni | int) == 2001\n"
        "          - pve_primary_node == 'pve1'\n"
    )
    res = subprocess.run(
        ["ansible-playbook", "-i", str(inv), str(pb)],
        cwd=str(ANSIBLE),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Override check failed:\n{res.stdout}\n{res.stderr}"


def test_create_rejects_invalid_vm_input():
    res = subprocess.run(
        [
            "ansible-playbook", "-i", "inventory.example.yml", "--check",
            "playbooks/create.yml",
            "-e", '{"vm":{"name":"bad","profile":"unknown","os":"debian-13","ssh_user":"u","ssh_public_key":"ssh-ed25519 AAAA"}}',
        ],
        cwd=str(ANSIBLE),
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    assert "Invalid vm declaration" in res.stdout


def test_publish_rejects_when_edge_disabled_or_invalid_domain():
    res = subprocess.run(
        [
            "ansible-playbook", "-i", "inventory.example.yml", "--check",
            "playbooks/publish.yml",
            "-e", '{"publish":{"vmid":103,"domain":"app.example.com","port":3001}}',
        ],
        cwd=str(ANSIBLE),
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    assert "Invalid publish declaration" in res.stdout

    res2 = subprocess.run(
        [
            "ansible-playbook", "-i", "inventory.example.yml", "--check",
            "playbooks/publish.yml",
            "-e", "vmp_edge_enabled=true",
            "-e", '{"publish":{"vmid":103,"domain":"bad domain!","port":3001}}',
        ],
        cwd=str(ANSIBLE),
        capture_output=True,
        text=True,
    )
    assert res2.returncode != 0
    assert "Invalid publish declaration" in res2.stdout


def test_delete_refuses_gateway_bastion_and_edge():
    for protected_id in (100, 101, 102):
        res = subprocess.run(
            [
                "ansible-playbook", "-i", "inventory.example.yml", "--check",
                "playbooks/delete.yml", "-e", f"vmid={protected_id}",
            ],
            cwd=str(ANSIBLE),
            capture_output=True,
            text=True,
        )
        assert res.returncode != 0
        assert "cannot be deleted" in res.stdout


def test_example_inventory_uses_neutral_placeholders():
    inv = yaml.safe_load((ANSIBLE / "inventory.example.yml").read_text())
    v = inv["all"]["vars"]
    assert v["pve_api_host"] == "pve.example.local"
    assert v["vmp_lan_cidr"] == "192.168.100.0/24"
    assert v["vmp_gateway_lan_ip"] == "192.168.100.250"
    assert v["vmp_bastion_lan_ip"] == "192.168.100.251"
    hosts = inv["all"]["children"]["proxmox_nodes"]["hosts"]
    assert list(hosts.keys()) == ["pve01"]
    assert hosts["pve01"]["ansible_host"] == "192.168.100.10"
    assert hosts["pve01"]["ansible_user"] == "proxmox-admin"
