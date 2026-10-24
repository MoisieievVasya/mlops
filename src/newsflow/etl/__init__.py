"""One module per task of the `ingest_month` DAG.

    extract -> validate -> transform -> load -> build_features -> quality_check

Every function takes the batch it is asked for as an argument and returns a small
dict of counts. None of them reads the wall clock: the batch is the data interval the
run owns, passed down from the orchestrator (design 6.1, diagram 2).
"""
