"""Completion evidence for either confirmed delivery or a quiet observation."""


def completion_stamp(receipt):
    if not isinstance(receipt, dict):
        return None
    notification = receipt.get('notification')
    if notification is None:
        return receipt.get('sent_at')  # Existing delivered observations stay valid.
    if (notification == 'sent' and receipt.get('changes_detected') is True
            and receipt.get('delivery_required') is True and receipt.get('delivery_complete') is True):
        return receipt.get('sent_at')
    if (notification == 'suppressed_no_changes' and receipt.get('changes_detected') is False
            and receipt.get('delivery_required') is False and receipt.get('delivery_complete') is True
            and not receipt.get('sent_at')):
        return receipt.get('completed_at')
    return None
