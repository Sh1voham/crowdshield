// node --test tests/ui
import { test } from "node:test";
import { cases } from "../../ui/js/selftest_cases.js";

for (const [name, fn] of cases) test(name, fn);
