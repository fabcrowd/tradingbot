import asyncio, aiohttp, json

async def test():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://localhost:8080/ws") as ws:
            for i in range(20):
                msg = await ws.receive(timeout=3)
                d = json.loads(msg.data)
                tp = d.get("type")
                data = d.get("data", {})
                has_scalp = "scalp" in data
                print(f"msg {i}: type={tp} has_scalp={has_scalp} keys={list(data.keys())[:5]}...")
                if tp == "snapshot" and has_scalp:
                    sk = data["scalp"]
                    print("  FOUND SCALP in snapshot!")
                    print("  scalp keys:", list(sk.keys())[:10])
                    return

    print("Never got scalp in snapshot after 20 messages")

asyncio.run(test())
