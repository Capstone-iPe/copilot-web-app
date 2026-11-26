const WORD = { asleep: "Asleep", waking: "Waking", up: "Awake", down: "Did not start" };
const wake = document.getElementById("wake");
const lead = document.getElementById("wake-lead");
const actions = document.getElementById("signin-actions");

function names(services) {
  const list = services.map((service) => service.name);
  if (list.length < 2) return list.join("");
  return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

function line(services) {
  const waking = services.filter((service) => service.state === "waking");
  if (waking.length === 1) return `Waking ${waking[0].name}. Render is still serving its start page.`;
  if (waking.length > 1) return `Waking ${names(waking)}.`;
  if (services.some((service) => service.state === "down")) return "A service answered, and it did not start. Checking again.";
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
    lead.textContent = "The lights are on.";
    actions.hidden = false;
    wake.setAttribute("aria-busy", "false");
    return true;
  }
  lead.textContent = line(body.services || []);
  return false;
}

async function tick() {
  try {
    const response = await fetch("/ready", { headers: { Accept: "application/json" } });
    if (paint(await response.json())) return;
  } catch (_error) {
    lead.textContent = "Still trying to reach the services.";
  }
  window.setTimeout(tick, 3000);
}

wake.setAttribute("aria-busy", "true");
tick();
