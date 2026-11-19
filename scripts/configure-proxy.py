"""服务器端隔离代理配置。依赖已有 mihomo 与 python3-yaml，不输出节点凭据。"""
import argparse
import json
import os
import pathlib
import re
import secrets
import subprocess
import urllib.request

import yaml

ROOT = pathlib.Path("/etc/news-fetch-proxy")
ALLOWED_KEYS = {"name", "type", "server", "port", "cipher", "password", "udp", "tfo", "uuid", "alterId", "tls", "servername", "sni", "network", "ws-opts", "grpc-opts", "client-fingerprint", "reality-opts", "flow", "plugin", "plugin-opts", "username", "auth-str", "obfs", "obfs-password", "up", "down", "congestion-controller", "disable-sni", "reduce-rtt", "udp-relay-mode"}


def configure(raw):
    data = yaml.safe_load(raw)
    allowed = {"ss", "ssr", "vmess", "vless", "trojan", "hysteria2", "tuic", "http", "socks5"}
    proxies = []
    for entry in data.get("proxies", []):
        if not isinstance(entry, dict) or entry.get("type") not in allowed:
            continue
        if not all(entry.get(key) for key in ("name", "server", "port")):
            continue
        if re.search("剩余|到期|流量|官网|过期|套餐", entry["name"]):
            continue
        proxies.append({k: v for k, v in entry.items() if k in ALLOWED_KEYS})
    if not proxies:
        raise ValueError("No supported nodes")
    names = list(dict.fromkeys(p["name"] for p in proxies))
    if len(names) != len(proxies):
        raise ValueError("Duplicate proxy names")
    config_path = ROOT / "config.yaml"
    secret = secrets.token_hex(24)
    if config_path.exists():
        secret = yaml.safe_load(config_path.read_text())["secret"]
        # 订阅刷新后优先复用已验证可用的节点，避免等待第一次全量测速。
        try:
            request = urllib.request.Request("http://127.0.0.1:19090/proxies/NEWS-AUTO", headers={"Authorization": "Bearer " + secret})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=5) as response:
                selected = json.load(response).get("now")
            if selected in names:
                names.remove(selected)
                names.insert(0, selected)
        except Exception:
            pass
    config = {"mixed-port": 17890, "allow-lan": False, "bind-address": "127.0.0.1", "ipv6": False,
              "mode": "rule", "log-level": "silent", "external-controller": "127.0.0.1:19090", "secret": secret,
              "proxies": proxies, "proxy-groups": [
                  {"name": "NEWS-OUT", "type": "select", "proxies": ["NEWS-AUTO"] + names},
                  {"name": "NEWS-AUTO", "type": "url-test", "proxies": names, "url": "https://www.gstatic.com/generate_204", "interval": 300, "tolerance": 100, "lazy": False}],
              "rules": ["MATCH,NEWS-OUT"]}
    temp = ROOT / "config.next.yaml"
    temp.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.chmod(temp, 0o640)
    import grp
    os.chown(temp, 0, grp.getgrnam("news-fetch-proxy").gr_gid)
    result = subprocess.run(["/usr/local/bin/mihomo", "-t", "-f", str(temp), "-d", "/var/lib/news-fetch-proxy"], capture_output=True, timeout=30)
    if result.returncode:
        temp.unlink(missing_ok=True)
        raise ValueError("Mihomo rejected config")
    os.replace(temp, config_path)
    print("Proxy configuration validated; supported nodes:", len(proxies))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="首次部署使用的本机已有订阅 YAML 文件")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    try:
        if args.input:
            raw = pathlib.Path(args.input).read_bytes()
        else:
            url = (ROOT / "subscription.url").read_text().strip()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": "http://127.0.0.1:17890", "http": "http://127.0.0.1:17890"}))
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(request, timeout=30) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("Subscription too large")
        configure(raw)
        if args.refresh:
            subprocess.run(["systemctl", "restart", "news-fetch-proxy.service"], check=True)
    except Exception as exc:
        # 错误内容可能含节点密码或订阅 URL，只记录异常类型。
        print("Proxy configuration failed:", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
