# Human approval signature migration

Human approvals now use the domain `gap.oob_approval.v2`. The canonical payload
includes `approved_at`, the exact `human_approval_timestamp`, alongside the
decision ID, proposal ID, authorization level, approver key ID and expiry.
The kernel decision signature format is unchanged.

Previously issued v1 human approvals are deliberately rejected. Obtain a new
human approval for an otherwise valid stored decision; expired decisions require
the deployment's authorization-renewal workflow. Do not reinterpret or relabel
an old signature as v2. The operator approval CLI produces v2 approvals.

If an upgraded gateway finds an old in-flight execution without a journaled
attempt decision, direct retry returns `reauthorization_required`. Use
`POST /v1/requests/{request_id}/reauthorize`, even if the original decision has
not expired. This preserves the old uncertainty and tool idempotency identity;
do not delete execution rows or submit a new request ID to work around it.

Approval times must include a timezone. An approval cannot precede the decision,
be dated in the future, or expire after the decision. An expiry is exclusive:
at that instant no new action may start. Keep approver and gateway clocks
synchronized; the verifier does not silently extend the interval for clock skew.

Custom approvers should sign `ExecutionFabric._oob_signed_message(decision)`
after populating both approval times and the approver key ID. Callers of
`CGALoop.approve_and_execute` must pass the exact signed timestamp via `timestamp`
or retain it on the decision. The loop no longer invents an unsigned timestamp.

The execution fabric rechecks decision and human approval validity before every
new action. A trusted `before_dispatch` deployment callback may additionally
check current policy and evidence. Any raised guard error stops the remaining
batch, records the failed attempt, and preserves already completed action
receipts. Completed actions are skipped on a valid retry. These checks cannot
cancel a tool call that has already started; cancellation requires tool support.
