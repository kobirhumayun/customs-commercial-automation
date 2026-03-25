from customs_automation.core.write_plan import (
    StagedWriteOperation,
    canonicalize_staged_write_plan,
    compute_staged_write_plan_hash,
)


def test_canonicalize_staged_write_plan_orders_by_mail_then_operation() -> None:
    operations = [
        StagedWriteOperation(2, 1, "Sheet1", 12, "amount", "", "100"),
        StagedWriteOperation(1, 2, "Sheet1", 10, "buyer_name", "", "ABC"),
        StagedWriteOperation(1, 1, "Sheet1", 10, "file_no", "", "P/26/0001"),
    ]

    serialized = canonicalize_staged_write_plan(operations)

    first = serialized.find('"column_key":"file_no"')
    second = serialized.find('"column_key":"buyer_name"')
    third = serialized.find('"column_key":"amount"')
    assert first < second < third


def test_compute_staged_write_plan_hash_is_stable_for_same_content() -> None:
    operation_a = StagedWriteOperation(1, 1, "Sheet1", 10, "file_no", "", "P/26/0001")
    operation_b = StagedWriteOperation(1, 2, "Sheet1", 10, "buyer_name", "", "ABC")

    hash_one = compute_staged_write_plan_hash([operation_a, operation_b])
    hash_two = compute_staged_write_plan_hash([operation_b, operation_a])

    assert hash_one == hash_two
    assert len(hash_one) == 64
