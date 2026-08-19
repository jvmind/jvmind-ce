# JDK flags reference data (optional)

`validate_jvm_args` validates user-provided `-XX` flags against the official
JDK flag list. The reference files are **not bundled** with the CE build to
keep the wheel small — the tool degrades gracefully with an explanatory
message when they are absent.

To enable full flag validation, generate the reference files on a machine
with the matching JDK installed and drop them here before packaging:

```bash
for v in 8 11 17 21 25; do
  /path/to/jdk$v/bin/java -XX:+PrintFlagsFinal -version 2>/dev/null \
    | grep ' [:=] ' > jdk$v-flags.txt
done
```

Each file is the raw `-XX:+PrintFlagsFinal` output (one flag per line).

The directory is included in the wheel via `MANIFEST.in` so any files placed
here are shipped.