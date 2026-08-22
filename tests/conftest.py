from hypothesis import settings

settings.register_profile(
    "default",
    max_examples=50,
    stateful_step_count=30,
    derandomize=True,
    database=None,
    deadline=None,
    report_multiple_bugs=False,
    print_blob=True,
)
settings.load_profile("default")
