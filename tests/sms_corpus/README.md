# SMS corpus

One file per bank/format, one message per block separated by a blank line,
each block starting with a header line `# expect: <json>` giving the expected
parse. **Everything here is PROVISIONAL synthetic text written from common
Iranian bank SMS conventions** — the owner's real messages (with amounts and
names altered) replace these after the first deployment. A parser that only
passes on invented messages proves the code, not the banks.
