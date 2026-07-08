from temporalio import workflow, activity
from activities.fusion import fuse_pci_etrm

@workflow.defn(name="PCI_ETRM_Operator_Update")
class PCIEtrmRitual:
    @workflow.run
    async def run(self, payload: dict):
        result = await workflow.execute_activity(
            fuse_pci_etrm, 
            payload, 
            start_to_close_timeout=12,
            retry_policy={"maximum_attempts": 5}
        )
        return {"status": "ALL_DOWNSTREAM_UPDATED", "pci": result["pci"], "etrm_action": result["action"]}
