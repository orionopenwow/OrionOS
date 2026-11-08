// Checks the polkit rules touched by the rescue/privacy work with a stand-in polkit (H9):
//   node os/privacy/tests/test_polkit_rules.js
// os/safety/50-orion-safety.rules, os/getapps/50-orion-getapps.rules, os/privacy/50-orion-privacy.rules
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..", "..");
const R = { YES: "yes", NO: "no", AUTH_SELF: "auth_self", AUTH_SELF_KEEP: "auth_self_keep",
            AUTH_ADMIN: "auth_admin", AUTH_ADMIN_KEEP: "auth_admin_keep", NOT_HANDLED: null };
let gateOpen = false;
function load(rel) {
  const rules = [];
  const polkit = {
    Result: R, addRule: f => rules.push(f),
    spawn: argv => {
      if (argv[0] !== "/opt/orion/getapps/orion-install-gate" || argv[1] !== "check") throw new Error("unexpected spawn");
      if (!gateOpen) throw new Error("exit 1");
      return "open\n";
    },
  };
  new Function("polkit", fs.readFileSync(path.join(root, rel), "utf8"))(polkit);
  return (a, s) => { for (const r of rules) { const v = r(a, s); if (v !== undefined && v !== null) return v; } return null; };
}
const act = (id, vars = {}) => ({ id, lookup: k => vars[k] });
const me = { user: "orion", local: true, active: true };
const away = { ...me, active: false };
const agent = { ...me, user: "orion-agent" };
const cl = c => ({ command_line: c });
const FH = "/opt/orion/safety/orion-freeze-helper";
const KEY = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE" + "A".repeat(88);

const safety = load("safety/50-orion-safety.rules");
const getapps = load("getapps/50-orion-getapps.rules");
const privacy = load("privacy/50-orion-privacy.rules");

const cases = [
  // ---- safety: freeze any time, unfreeze needs a password or a phone token
  [safety, act("ai.orion.safety.freeze", cl(`${FH} on`)), me, "yes"],
  [safety, act("ai.orion.safety.freeze", cl(`${FH} status`)), me, "yes"],
  [safety, act("ai.orion.safety.freeze", cl(`${FH} off`)), me, "auth_self"],
  [safety, act("ai.orion.safety.freeze", cl(`${FH} off`)), away, null],
  [safety, act("ai.orion.safety.freeze", cl(`${FH} off --approval /run/user/1000/orion-approvals/ab12.json`)), me, "yes"],
  [safety, act("ai.orion.safety.freeze", cl(`${FH} off --approval /run/../etc/shadow`)), me, null],
  [safety, act("ai.orion.safety.freeze", cl(`${FH} off --approval relative.json`)), me, null],
  [safety, act("ai.orion.safety.freeze", cl(`${FH} off; rm -rf /`)), me, null],
  [safety, act("ai.orion.safety.freeze", cl(`${FH} pin-phone abcdefgh12 ${KEY}`)), me, "auth_self"],
  [safety, act("ai.orion.safety.freeze", cl(`${FH} pin-phone abcdefgh12 ${KEY}`)), away, null],
  [safety, act("ai.orion.safety.freeze", cl(`${FH} pin-phone x ${KEY}`)), me, null],
  [safety, act("ai.orion.safety.freeze", cl(`${FH} on`)), agent, null],
  // snapshots: take/list/status yes, undo with your password, restore never here
  [safety, act("ai.orion.safety.snapshot", cl("/usr/local/bin/orion-snapshot take --kind pre-update")), me, "yes"],
  [safety, act("ai.orion.safety.snapshot", cl("/usr/local/bin/orion-snapshot undo")), me, "auth_self_keep"],
  [safety, act("ai.orion.safety.snapshot", cl("/usr/local/bin/orion-snapshot undo --yes")), me, "auth_self_keep"],
  [safety, act("ai.orion.safety.snapshot", cl("/usr/local/bin/orion-snapshot undo")), away, null],
  [safety, act("ai.orion.safety.snapshot", cl("/usr/local/bin/orion-snapshot restore 3 --yes")), me, null],
  [safety, act("ai.orion.safety.selfrun", cl("/opt/orion/safety/orion-self-helper vacuum-journal")), me, "yes"],
  // ---- Get apps: updates yes; a new app needs the gate or a password
  [getapps, act("org.freedesktop.Flatpak.app-update"), away, "yes"],
  [getapps, act("org.freedesktop.Flatpak.runtime-install"), away, "yes"],
  [getapps, act("org.freedesktop.Flatpak.app-install"), me, "auth_self_keep"],
  [getapps, act("org.freedesktop.Flatpak.app-install"), away, null],
  [getapps, act("org.freedesktop.Flatpak.app-uninstall"), me, "auth_self_keep"],
  [getapps, act("org.freedesktop.Flatpak.configure-remote"), me, null],
  [getapps, act("org.freedesktop.Flatpak.app-install"), agent, null],
  [getapps, act("ai.orion.getapps.gate", cl("/opt/orion/getapps/orion-install-gate open")), me, "auth_self"],
  [getapps, act("ai.orion.getapps.gate", cl("/opt/orion/getapps/orion-install-gate open --approval /run/user/1000/t.json")), away, "yes"],
  [getapps, act("ai.orion.getapps.gate", cl("/opt/orion/getapps/orion-install-gate close")), me, "yes"],
  [getapps, act("org.freedesktop.systemd1.manage-units", { unit: "orion-share-firewall.service", verb: "start" }), me, "yes"],
  [getapps, act("org.freedesktop.systemd1.manage-units", { unit: "ssh.service", verb: "start" }), me, null],
  // ---- privacy: first password only from the session, phone port open/close/sync
  [privacy, act("ai.orion.privacy.first-password", cl("/opt/orion/privacy/orion-first-password")), me, "yes"],
  [privacy, act("ai.orion.privacy.first-password", cl("/opt/orion/privacy/orion-first-password")), away, null],
  [privacy, act("ai.orion.privacy.first-password", cl("/opt/orion/privacy/orion-first-password x")), me, null],
  [privacy, act("ai.orion.privacy.first-password", cl("/opt/orion/privacy/orion-first-password")), agent, null],
  [privacy, act("ai.orion.privacy.phone-port", cl("/opt/orion/privacy/orion-phone-port open 600")), me, "yes"],
  [privacy, act("ai.orion.privacy.phone-port", cl("/opt/orion/privacy/orion-phone-port open")), me, "yes"],
  [privacy, act("ai.orion.privacy.phone-port", cl("/opt/orion/privacy/orion-phone-port sync")), away, "yes"],
  [privacy, act("ai.orion.privacy.phone-port", cl("/opt/orion/privacy/orion-phone-port open 99999")), me, null],
  [privacy, act("ai.orion.privacy.phone-port", cl("/opt/orion/privacy/orion-phone-port open 600")), agent, null],
];
let bad = 0;
for (const [rule, a, s, want] of cases) {
  const got = rule(a, s) ?? null;
  if (got !== want) { bad++; console.log("FAIL", a.id, a.lookup("command_line") || "", JSON.stringify(s), "got", got, "want", want); }
}
// the install gate open: a new app goes through without asking
gateOpen = true;
const g = getapps(act("org.freedesktop.Flatpak.app-install"), away);
if (g !== "yes") { bad++; console.log("FAIL gate open: got", g); }
console.log(bad ? `${bad} failed` : `polkit rules: ${cases.length + 1} cases ok`);
process.exit(bad ? 1 : 0);
