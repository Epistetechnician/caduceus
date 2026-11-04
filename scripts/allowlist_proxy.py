"""CONNECT-only HTTPS proxy that allows a few hostnames. Runs on the VM as a normal user; the agent user can reach only this port.
Usage: allowlist_proxy.py <port> <denied.log> <suffix> [suffix...]"""
import asyncio
import os
import sys
import time

PORT, LOG, ALLOW = int(sys.argv[1]), sys.argv[2], tuple(sys.argv[3:])


def log(msg):
    with open(LOG, "a") as fh:
        fh.write("%d %s\n" % (time.time(), msg))


async def pipe(r, w):
    try:
        while (d := await r.read(65536)):
            w.write(d)
            await w.drain()
    except Exception:
        pass
    finally:
        w.close()


async def handle(cr, cw):
    try:
        line = (await cr.readline()).decode("latin1").strip()
        while (await cr.readline()).strip():
            pass
        parts = line.split()
        if len(parts) < 2 or parts[0] != "CONNECT":
            log("DENY non-connect %s" % line[:80])
            cw.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        else:
            host, _, port = parts[1].rpartition(":")
            if not any(host == s or host.endswith("." + s) for s in ALLOW):
                log("DENY %s" % parts[1])
                cw.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            else:
                ur, uw = await asyncio.open_connection(host, int(port))
                cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await asyncio.gather(pipe(cr, uw), pipe(ur, cw))
                return
        await cw.drain()
    except Exception as e:
        log("ERR %r" % (e,))
    finally:
        cw.close()


async def main():
    srv = await asyncio.start_server(handle, os.environ.get("BIND", "127.0.0.1"), PORT)
    async with srv:
        await srv.serve_forever()

asyncio.run(main())
