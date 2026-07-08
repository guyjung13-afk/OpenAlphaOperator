import pandas as pd
import httpx
import redis.asyncio as redis

async def fuse_pci_etrm(change: dict):
    # Connect using Base_SQL_Ingestion pattern
    df = pd.read_sql("SELECT * FROM DT_PCI_ADJUSTED WHERE change_key=%s", get_conn(), params=[change["key"]])
    insight = await grok_reason("PCI + ETRM analysis from operator update", df.iloc[0].to_dict())
    
    r = await redis.from_url("redis://redis")
    await r.setex("latest_pci_etrm", 7200, df.to_json())
    
    await send_ziti_burst(df, insight["provider_msg"])
    await trigger_bi_refresh()
    return {"pci": float(df["pci_index"].iloc[0]), "action": insight["action"]}
