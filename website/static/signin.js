const WORD = { asleep: "Asleep", waking: "Waking", up: "Awake", down: "Did not start" };
const wake = document.getElementById("wake");
const lead = document.getElementById("wake-lead");
const note = document.getElementById("wake-note");
const actions = document.getElementById("signin-actions");

function names(services) {
  const list = services.map((service) => service.name);
  if (list.length < 2) return list.join("");
  return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

function line(services) {
  const waking = services.filter((service) => service.state === "waking");
  if (waking.length === 1) return `Still starting ${waking[0].name}.`;
  if (waking.length > 1) return `Still starting ${names(waking)}.`;
  if (services.some((service) => service.state === "down")) return "One service answered, and it did not start. Checking again.";
  return "Checking each service.";
}

function paint(body) {
  for (const service of body.services || []) {
    const row = wake.querySelector(`[data-id="${service.id}"]`);
    if (!row) continue;
    row.dataset.state = service.state;
    row.querySelector("small").textContent = WORD[service.state] || service.state;
  }
  if (body.ready) {
    lead.textContent = "All three are up.";
    note.textContent = "They sleep again after about 15 minutes with no messages. The next message starts them if they have.";
    actions.hidden = false;
    wake.setAttribute("aria-busy", "false");
    return true;
  }
  lead.textContent = line(body.services || []);
  return false;
}

function shown() {
  return [...wake.querySelectorAll("[data-id]")].map((row) => ({
    name: row.querySelector("b").textContent,
    state: row.dataset.state,
  }));
}

async function tick() {
  try {
    await holdHealth([wake.dataset.copilot, wake.dataset.onb, wake.dataset.corpus]);
    const response = await fetch("/ready", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(String(response.status));
    if (paint(await response.json())) return;
  } catch (_error) {
    const services = shown();
    lead.textContent = services.some((service) => service.state !== "asleep")
      ? line(services)
      : "Still trying to reach the services.";
  }
  window.setTimeout(tick, 3000);
}

wake.setAttribute("aria-busy", "true");
tick();
