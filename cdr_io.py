# =============================================================================
# cdr_io.py - Shared columnar I/O (Parquet) for the two large pipeline files:
#   cdr_master  (step 1 output)
#   cdr_enriched (step 2 output)
#
# WHY PARQUET: these files grow continuously. In plain CSV they inflate disk
# usage and I/O time for every step that reads them in full (steps 3–9,
# strategy, optimization). Parquet (columnar + zstd compression via pyarrow)
# gives the same content in a much smaller file with faster sequential reads.
#
# INTERFACE COMPATIBILITY: ParquetDictWriter / read_parquet_rows() expose the
# same row-dict interface as csv.DictWriter / csv.DictReader. Values are always
# strings, "" instead of None/NaN for empties. No consumer script changes its
# business logic — only the open() + csv block is replaced with these helpers.
#
# STREAMING WRITES (fix for RAM exhaustion at scale):
# The original implementation buffered ALL rows as Python dicts before writing.
# At production scale (~weeks of history, millions of rows) this caused ~4 GB
# RAM usage and disk swapping. ParquetDictWriter writes in batches of
# `batch_size` rows (default 100k) via pq.ParquetWriter — memory is bounded
# to one batch regardless of total file size.
#
# APPEND MODE streaming:
# mode="a" copies the existing file AND the new rows in batches — neither is
# fully loaded into memory. The final file is written to a temp path and
# atomically replaced via os.replace().
#
# WINDOWS FILE HANDLE NOTE:
# All pq.ParquetFile objects are explicitly .close()d before any os.replace()
# or os.remove() call. Linux allows renaming open files (POSIX); Windows raises
# PermissionError (WinError 5) if any handle remains open — even within the
# same process. Explicit close, never relying on GC timing.
# =============================================================================
import os
import pyarrow as pa
import pyarrow.parquet as pq


class ParquetDictWriter:
    """Drop-in replacement for csv.DictWriter targeting Parquet output.

    Usage:
        w = ParquetDictWriter(path, fieldnames, mode="w")  # or mode="a"
        w.writeheader()   # no-op, kept for drop-in compatibility
        w.writerow({...}) # as many times as needed
        w.close()

    Parameters
    ----------
    path : str
        Output file path.
    fieldnames : list[str]
        Column names (all stored as pa.string()).
    mode : str
        "w" = overwrite; "a" = append to existing file if present.
    batch_size : int
        Rows per Arrow batch. Default 100_000.
    on_row : callable or None
        Optional callback(row_dict) called on each writerow() before the row
        is flushed from the buffer. Used by step1 to track processed
        (date, tool) pairs without keeping full rows in memory.
    """

    def __init__(self, path, fieldnames, mode="w", batch_size=100_000, on_row=None):
        self.path = path
        self.fieldnames = list(fieldnames)
        self.mode = mode
        self.batch_size = batch_size
        self.on_row = on_row
        self._schema = pa.schema([(k, pa.string()) for k in self.fieldnames])
        self._buffer = []
        self._writer = None
        self._path_tmp = path + ".tmp_new"
        self._has_rows = False

    def writeheader(self):
        pass  # schema is the Parquet header — nothing to write separately

    def writerow(self, row):
        # Cast all values to str, "" for None — same as csv.DictWriter/DictReader
        record = {k: ("" if row.get(k) is None else str(row.get(k, ""))) for k in self.fieldnames}
        if self.on_row is not None:
            self.on_row(record)
        self._buffer.append(record)
        if len(self._buffer) >= self.batch_size:
            self._flush()

    def _flush(self):
        if not self._buffer:
            return
        columns = {k: [r[k] for r in self._buffer] for k in self.fieldnames}
        table = pa.table(columns, schema=self._schema)
        if self._writer is None:
            self._writer = pq.ParquetWriter(self._path_tmp, self._schema, compression="zstd")
        self._writer.write_table(table)
        self._has_rows = True
        self._buffer = []

    def close(self):
        self._flush()
        if self._writer is not None:
            self._writer.close()

        if self.mode == "a" and os.path.exists(self.path):
            # Stream existing file + new rows into a combined temp, then
            # atomically replace — neither is fully in memory simultaneously.
            path_combined = self.path + ".tmp_combined"
            final_writer = pq.ParquetWriter(path_combined, self._schema, compression="zstd")

            pf_old = pq.ParquetFile(self.path)
            for batch in pf_old.iter_batches(batch_size=self.batch_size):
                t = pa.Table.from_batches([batch]).select(self.fieldnames).cast(self._schema)
                final_writer.write_table(t)
            pf_old.close()  # must close BEFORE os.replace on Windows

            if self._has_rows:
                pf_new = pq.ParquetFile(self._path_tmp)
                for batch in pf_new.iter_batches(batch_size=self.batch_size):
                    final_writer.write_table(pa.Table.from_batches([batch]))
                pf_new.close()  # same reason

            final_writer.close()
            if self._has_rows:
                os.remove(self._path_tmp)
            os.replace(path_combined, self.path)

        elif self._has_rows:
            os.replace(self._path_tmp, self.path)
        else:
            # mode="w" (or "a" with no prior file) and zero rows —
            # still write a valid empty Parquet with the correct schema.
            empty = pa.table({k: pa.array([], type=pa.string()) for k in self.fieldnames})
            pq.write_table(empty, self.path, compression="zstd")

    # Context manager support
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def read_parquet_columns(path, columns):
    """Read only the requested columns from a Parquet file.

    Parquet is columnar — this reads only the requested columns from disk,
    not the full file. Much faster than read_parquet_rows() when only 2–3
    fields are needed from a wide file (e.g. loading dedup keys from master).

    Returns dict {column_name: [values...]}.
    """
    table = pq.read_table(path, columns=columns)
    return {c: table.column(c).to_pylist() for c in columns}


def read_parquet_rows(path, batch_size=100_000):
    """Generator of row dicts — same interface as csv.DictReader.

    Values are always strings, "" for nulls. Reads in batches of `batch_size`
    rows so memory usage is bounded to one batch regardless of file size
    (same behavior as csv.DictReader reading line-by-line from disk).

    The ParquetFile is explicitly closed on exhaustion or abandonment
    (break/GC) via try/finally — required for Windows compatibility.
    """
    pf = pq.ParquetFile(path)
    try:
        for batch in pf.iter_batches(batch_size=batch_size):
            col_names = batch.schema.names
            data = {c: batch.column(c).to_pylist() for c in col_names}
            n = batch.num_rows
            for i in range(n):
                yield {c: ("" if data[c][i] is None else str(data[c][i])) for c in col_names}
    finally:
        pf.close()


def get_parquet_columns(path):
    """Return column names without reading any row data."""
    pf = pq.ParquetFile(path)
    names = pf.schema_arrow.names
    pf.close()
    return names
