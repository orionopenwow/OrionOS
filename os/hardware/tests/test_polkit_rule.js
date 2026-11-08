// Checks os/hardware/50-orion-hardware.rules with a stand-in polkit:   node os/hardware/tests/test_polkit_rule.js
const fs = require("fs");
let rule;
const polkit = { Result: { YES: "yes", NO: "no", NOT_HANDLED: null }, addRule: f => { rule = f; } };
eval(fs.readFileSync(process.argv[2] || require("path").join(__dirname, "..", "50-orion-hardware.rules"), "utf8"));
const act = (id, vars = {}) => ({ id, lookup: k => vars[k] });
const me = { user: "orion", local: true, active: true };
const cases = [
  [act("org.opensuse.cupspkhelper.mechanism.printeraddremove"), me, "yes"],
  [act("org.opensuse.cupspkhelper.mechanism.printer-local-edit"), me, "yes"],
  [act("org.opensuse.cupspkhelper.mechanism.server-settings"), me, null],
  [act("org.opensuse.cupspkhelper.mechanism.all-edit"), me, null],
  [act("org.opensuse.cupspkhelper.mechanism.printeraddremove"), { ...me, active: false }, null],
  [act("org.opensuse.cupspkhelper.mechanism.printeraddremove"), { ...me, user: "orion-agent" }, null],
  [act("org.opensuse.cupspkhelper.mechanism.printeraddremove"), { ...me, local: false }, null],
  [act("org.freedesktop.systemd1.manage-units", { verb: "start", unit: "orion-papersize@letter.service" }), me, "yes"],
  [act("org.freedesktop.systemd1.manage-units", { verb: "start", unit: "orion-papersize@x;y.service" }), me, null],
  [act("org.freedesktop.systemd1.manage-units", { verb: "stop", unit: "orion-papersize@a4.service" }), me, null],
  [act("org.freedesktop.systemd1.manage-units", { verb: "start", unit: "cups.service" }), me, null],
];
let bad = 0;
for (const [a, s, want] of cases) {
  const got = rule(a, s) ?? null;
  if (got !== want) { bad++; console.log("FAIL", a.id, JSON.stringify(s), got, want); }
}
console.log(bad ? `${bad} failed` : `polkit rule: ${cases.length} cases ok`);
process.exit(bad ? 1 : 0);
