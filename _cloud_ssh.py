# -*- coding: utf-8 -*-
"""云 GPU 远程执行 helper：python _cloud_ssh.py "命令"  |  python _cloud_ssh.py --put 本地文件 /远程路径

连接信息从环境变量读取（见 .env.example）：CLOUD_SSH_HOST / CLOUD_SSH_PORT / CLOUD_SSH_USER / CLOUD_SSH_PASSWORD
"""
import os
import sys

HOST = os.environ.get("CLOUD_SSH_HOST", "")
PORT = int(os.environ.get("CLOUD_SSH_PORT", "22"))
USER = os.environ.get("CLOUD_SSH_USER", "root")
PWD = os.environ.get("CLOUD_SSH_PASSWORD", "")

if not (HOST and PWD):
    sys.exit("请先在 .env 中配置 CLOUD_SSH_HOST / CLOUD_SSH_USER / CLOUD_SSH_PASSWORD")


def client():
    import paramiko

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PWD,
              timeout=30, banner_timeout=30, auth_timeout=30)
    return c


def run(cmd: str, timeout: int = 300):
    c = client()
    try:
        _, out, err = c.exec_command(cmd, timeout=timeout)
        o, e = out.read().decode("utf-8", "ignore"), err.read().decode("utf-8", "ignore")
        rc = out.channel.recv_exit_status()
        return rc, o, e
    finally:
        c.close()


def put(local: str, remote: str):
    # seetacloud 的 SFTP 子系统被隔离：改用 exec 通道流式写入
    c = client()
    try:
        # base64 内嵌命令行（stdin 大流量被平台限制）
        import base64, time
        CH = 32 * 1024
        with open(local, "rb") as f:
            first = True
            while True:
                chunk = f.read(CH)
                if not chunk:
                    break
                b64 = base64.b64encode(chunk).decode()
                op = ">" if first else ">>"
                first = False
                rc, o, e = run(f"printf %s '{b64}' | base64 -d {op} '{remote}'", timeout=120)
                if rc != 0:
                    raise RuntimeError(f"块上传失败 rc={rc} {e[:120]}")
        return "uploaded-chunked"
    finally:
        c.close()


if __name__ == "__main__":
    if sys.argv[1] == "--put":
        print(put(sys.argv[2], sys.argv[3]))
    else:
        rc, o, e = run(sys.argv[1])
        sys.stdout.write(o)
        if e.strip():
            sys.stderr.write("[stderr] " + e[-2000:])
        sys.exit(rc)
