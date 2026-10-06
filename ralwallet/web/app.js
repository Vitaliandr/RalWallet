// фронт без фреймворков и сборки
const $ = (s) => document.querySelector(s);

const SIGN = { RUB: "₽", USD: "$", EUR: "€" };
const ACC_NAME = { RUB: "Рублёвый счёт", USD: "Долларовый счёт", EUR: "Счёт в евро" };
const PAGE = 30;

let token = load("token");
let accounts = [];
let currentId = null;
let ops = [];
let transferKey = null;
let transferBy = "account";
let authMode = "login";
let me = null;

//в приватном режиме localStorage кидает ошибку
function load(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function save(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* ну и ладно, просто не запомним */ }
}

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (token) headers.Authorization = "Bearer " + token;

  let r;
  try {
    r = await fetch(path, { ...opts, headers });
  } catch {
    // 0 = до сервера не дошли
    throw new ApiError("Нет связи с сервером", 0);
  }

  let data = null;
  try { data = await r.json(); } catch { /* пустой ответ */ }

  if (r.status === 401 && token) {
    //токен протух, отправляем на вход
    logout();
    throw new ApiError("Сессия закончилась, войдите заново", 401);
  }
  if (!r.ok) {
    const msg = String((data && data.detail) || "Ошибка " + r.status);
    throw new ApiError(msg.charAt(0).toUpperCase() + msg.slice(1), r.status);
  }
  return { data, status: r.status };
}

//---------- деньги ----------

// суммы строкой, во float не переводим
function money(value, cur) {
  let [int, frac = "00"] = String(value).split(".");
  const minus = int.startsWith("-");
  if (minus) int = int.slice(1);
  int = int.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return (minus ? "−" : "") + int + "," + frac.padEnd(2, "0") + " " + (SIGN[cur] || cur);
}

function toKopecks(value) {
  const [int, frac = ""] = String(value).replace("-", "").split(".");
  return Number(int) * 100 + Number(frac.padEnd(2, "0"));
}

function fromKopecks(k) {
  return Math.floor(k / 100) + "." + String(k % 100).padStart(2, "0");
}

//"1 500,5" -> "1500.5", null если ввели ерунду
function parseAmount(text) {
  const s = text.replace(/[\s ]/g, "").replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(s) || Number(s) === 0) return null;
  return s;
}

function newKey() {
  //randomUUID только на https/localhost
  if (crypto.randomUUID) return crypto.randomUUID();
  return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- вход ----------

function showAuth() {
  $("#main").hidden = true;
  $("#auth").hidden = false;
  $("#auth-form [name=email]").focus();
}

function setAuthMode(mode) {
  authMode = mode;
  document.querySelectorAll("#auth .tab").forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
  $("#auth-submit").textContent = mode === "login" ? "Войти" : "Создать аккаунт";
  $(".hint-register").hidden = mode === "login";
  $(".register-only").hidden = mode === "login";
  $("#auth-form [name=password]").autocomplete = mode === "login" ? "current-password" : "new-password";
  $("#auth-error").textContent = "";
}

document.querySelectorAll("#auth .tab").forEach((t) => t.addEventListener("click", () => setAuthMode(t.dataset.mode)));

// дата рождения не может быть в будущем
const today = new Date().toISOString().slice(0, 10);
document.querySelectorAll("input[type=date]").forEach((i) => (i.max = today));

// пустое = null, сервер очистит поле
const orNull = (v) => (v.trim() === "" ? null : v.trim());

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const email = form.email.value.trim();
  const password = form.password.value;
  const err = $("#auth-error");
  err.textContent = "";

  if (!email || !password) {
    err.textContent = "Введите email и пароль";
    return;
  }
  if (authMode === "register" && password.length < 8) {
    err.textContent = "Пароль должен быть не короче 8 символов";
    return;
  }

  let profile = null;
  if (authMode === "register") {
    profile = {
      last_name: form.last_name.value.trim(),
      first_name: form.first_name.value.trim(),
      middle_name: orNull(form.middle_name.value),
      birth_date: form.birth_date.value,
      phone: orNull(form.phone.value),
    };
    if (!profile.last_name || !profile.first_name) {
      err.textContent = "Укажите фамилию и имя";
      return;
    }
    if (!profile.birth_date) {
      err.textContent = "Укажите дату рождения";
      return;
    }
  }

  const btn = $("#auth-submit");
  btn.disabled = true;
  try {
    if (authMode === "register") {
      await api("/users", { method: "POST", body: JSON.stringify({ email, password, ...profile }) });
    }
    const { data } = await api("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
    token = data.access_token;
    save("token", token);
    form.reset();
    await loadMain();
  } catch (ex) {
    err.textContent = ex.status === 401 ? "Неверный email или пароль" : ex.message;
  } finally {
    btn.disabled = false;
  }
});

function logout() {
  token = null;
  me = null;
  save("token", null);
  accounts = [];
  currentId = null;
  closeModal();
  if (location.hash) history.replaceState(null, "", location.pathname);
  setAuthMode("login");
  showAuth();
}

//отзываем токен и на сервере
$("#logout").addEventListener("click", () => {
  if (token) {
    fetch("/auth/logout", { method: "POST", headers: { Authorization: "Bearer " + token } }).catch(() => {});
  }
  logout();
});

// ---------- счета ----------

function displayName(u) {
  return u.first_name ? `${u.first_name} ${u.last_name || ""}`.trim() : u.email;
}

function renderMe() {
  $("#open-profile").textContent = displayName(me);
}

async function loadMain() {
  const { data } = await api("/users/me");
  me = data;
  renderMe();
  $("#auth").hidden = true;
  $("#main").hidden = false;
  await loadAccounts();
  route();
}

async function loadAccounts() {
  const { data } = await api("/accounts");
  accounts = data;

  if (!accounts.some((a) => a.id === currentId)) {
    // по умолчанию открываем основной счёт
    const main = accounts.find((a) => me && a.id === me.main_account_id);
    currentId = main ? main.id : accounts.length ? accounts[0].id : null;
  }
  renderAccounts();

  $(".layout").classList.toggle("no-acc", accounts.length === 0);
  $("#no-accounts").hidden = accounts.length > 0;
  $("#account-card").hidden = accounts.length === 0;
  if (currentId !== null) await selectAccount(currentId);
}

const isMain = (a) => me && a.id === me.main_account_id;

function renderAccounts() {
  $("#accounts-list").innerHTML = accounts.map((a) => `
    <button class="acc-item ${a.id === currentId ? "active" : ""}" data-id="${a.id}">
      <div class="acc-name">${ACC_NAME[a.currency] || esc(a.currency)} · №${a.id}${isMain(a) ? '<span class="acc-main">основной</span>' : ""}</div>
      <div class="acc-sum">${money(a.balance, a.currency)}</div>
    </button>`).join("");

  renderTotal();
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

// валюты не складываем, курса нет
function renderTotal() {
  const box = $("#total");
  //с одним счётом смысла нет
  if (accounts.length < 2) {
    box.hidden = true;
    return;
  }

  const sums = {};
  for (const a of accounts) sums[a.currency] = (sums[a.currency] || 0) + toKopecks(a.balance);

  // рубли первые, нули не показываем
  const order = ["RUB", "USD", "EUR"];
  const curs = Object.keys(sums).sort((x, y) => order.indexOf(x) - order.indexOf(y));
  let shown = curs.filter((c) => sums[c] > 0);
  if (!shown.length) shown = [curs[0]];

  const [main, ...rest] = shown;
  const n = accounts.length;
  box.innerHTML = `
    <div class="total-label">Общий баланс</div>
    <div class="total-main">${money(fromKopecks(sums[main]), main)}</div>
    ${rest.length ? `<div class="total-rest">${rest.map((c) => money(fromKopecks(sums[c]), c)).join("<span>·</span>")}</div>` : ""}
    <div class="total-count">по ${n} ${plural(n, "счёту", "счетам", "счетам")}</div>`;
  box.hidden = false;
}

$("#accounts-list").addEventListener("click", (e) => {
  const item = e.target.closest(".acc-item");
  if (item) selectAccount(Number(item.dataset.id));
});

async function selectAccount(id) {
  currentId = id;
  const acc = accounts.find((a) => a.id === id);
  renderAccounts();
  $("#acc-number").textContent = "№" + acc.id;
  $("#acc-balance").textContent = money(acc.balance, acc.currency);
  await loadHistory(true);
}

$("#copy-number").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(String(currentId));
    toast("Номер счёта скопирован");
  } catch {
    toast("Номер счёта: " + currentId);
  }
});

//---------- история ----------

async function loadHistory(reset) {
  const id = currentId;
  let url = `/accounts/${id}/operations?limit=${PAGE}`;
  if (!reset && ops.length) url += "&before_id=" + ops[ops.length - 1].id;

  const { data } = await api(url);
  if (id !== currentId) return; // пока грузили, переключили счёт

  ops = reset ? data : ops.concat(data);
  $("#more").hidden = data.length < PAGE;
  renderHistory();
}

$("#more").addEventListener("click", () => loadHistory(false));

$("#history").addEventListener("click", (e) => {
  const row = e.target.closest(".op");
  if (!row) return;
  const op = ops.find((o) => o.id === Number(row.dataset.id));
  if (op) showOperation(op);
});

function detailsRow(label, value, cls = "") {
  return `<div class="details-row"><span>${label}</span><span class="${cls}">${value}</span></div>`;
}

//счёт + имя владельца: "№7268681065, Пётр И."
function party(accId, name) {
  return `№${accId}` + (name ? `, ${esc(name)}` : "");
}

let opShown = null;
let opSeq = 0;

async function showOperation(op) {
  const acc = accounts.find((a) => a.id === currentId);
  const modal = openModal("op-modal");
  const incoming = op.kind !== "transfer_out";
  modal.classList.toggle("op-in", incoming);
  modal.classList.toggle("op-out", !incoming);
  opShown = null;
  const seq = ++opSeq;

  const titles = { deposit: "Пополнение", transfer_in: "Входящий перевод", transfer_out: "Исходящий перевод" };
  $("#op-icon").textContent = op.kind === "deposit" ? "+" : incoming ? "↓" : "↑";
  $("#op-title").textContent = titles[op.kind];
  $("#op-when").textContent = fullDate(op.created_at, true);
  $("#op-amount").textContent = (incoming ? "+" : "") + money(op.amount, acc.currency);
  $("#op-repeat").hidden = true;

  const after = detailsRow("Остаток после операции", money(op.balance_after, acc.currency));
  const status = detailsRow("Статус", "Выполнено", "status-ok");

  if (op.kind === "deposit") {
    $("#op-details").innerHTML = status + detailsRow("Счёт", `№${acc.id}`) + after;
    return;
  }

  //догружаем имена
  $("#op-details").innerHTML = `<div class="details-row"><span>Загрузка…</span><span></span></div>`;
  try {
    const { data: t } = await api(`/transfers/${op.transfer_id}`);
    //пока грузили, окно закрыли или открыли другую операцию
    if (seq !== opSeq || $("#overlay").hidden) return;
    opShown = t;
    $("#op-details").innerHTML =
      status +
      detailsRow("Откуда", party(t.from_account_id, t.from_name)) +
      detailsRow("Куда", party(t.to_account_id, t.to_name)) +
      after +
      detailsRow("Номер операции", t.id);
    $("#op-repeat").hidden = incoming;
  } catch (ex) {
    $("#op-details").innerHTML = "";
    modal.querySelector(".form-error").textContent = ex.message;
  }
}

$("#op-repeat").addEventListener("click", () => {
  if (!opShown) return;
  openTransfer({ from: opShown.from_account_id, to: String(opShown.to_account_id), amount: opShown.amount });
});

function dayLabel(d) {
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  if (d.toDateString() === today.toDateString()) return "Сегодня";
  if (d.toDateString() === yesterday.toDateString()) return "Вчера";
  const opts = { day: "numeric", month: "long" };
  if (d.getFullYear() !== today.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString("ru-RU", opts);
}

function renderHistory() {
  const acc = accounts.find((a) => a.id === currentId);
  if (!ops.length) {
    $("#history").innerHTML = `<div class="history-empty">Операций пока нет</div>`;
    return;
  }

  let html = "";
  let lastDay = "";
  for (const op of ops) {
    const d = new Date(op.created_at);
    const day = dayLabel(d);
    if (day !== lastDay) {
      html += `<div class="day">${esc(day)}</div>`;
      lastDay = day;
    }

    let title, icon, cls;
    if (op.kind === "deposit") {
      title = "Пополнение"; icon = "+"; cls = "op-in";
    } else if (op.kind === "transfer_in") {
      title = `Перевод со счёта №${op.other_account_id}`; icon = "↓"; cls = "op-in";
    } else {
      title = `Перевод на счёт №${op.other_account_id}`; icon = "↑"; cls = "op-out";
    }

    const time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    const sum = (op.amount.startsWith("-") ? "" : "+") + money(op.amount, acc.currency);

    html += `
      <div class="op ${cls}" data-id="${op.id}">
        <div class="op-icon">${icon}</div>
        <div>
          <div class="op-title">${esc(title)}</div>
          <div class="op-sub">${time}</div>
        </div>
        <div class="op-sum">${sum}
          <div class="op-after">остаток ${money(op.balance_after, acc.currency)}</div>
        </div>
      </div>`;
  }
  $("#history").innerHTML = html;
}

//---------- модалки ----------

function openModal(id) {
  $("#overlay").hidden = false;
  document.querySelectorAll(".modal").forEach((m) => (m.hidden = m.id !== id));
  const form = $("#" + id);
  form.reset();
  form.querySelector(".form-error").textContent = "";
  const first = form.querySelector("input, select");
  if (first) setTimeout(() => first.focus(), 0);
  return form;
}

function closeModal() {
  $("#overlay").hidden = true;
}

document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", closeModal));
$("#overlay").addEventListener("mousedown", (e) => {
  if (e.target.id === "overlay") closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

// от двойного клика
async function busy(form, fn) {
  const btn = form.querySelector("[type=submit]");
  const err = form.querySelector(".form-error");
  btn.disabled = true;
  err.textContent = "";
  try {
    await fn(err);
  } finally {
    btn.disabled = false;
  }
}

// перевод
//prefill для "Повторить"
function openTransfer(prefill = {}) {
  const form = openModal("transfer-form");
  form.from.innerHTML = accounts.map((a) =>
    `<option value="${a.id}">№${a.id}, ${money(a.balance, a.currency)}</option>`).join("");
  form.from.value = prefill.from || currentId;
  setTransferBy("account");
  if (prefill.to) {
    form.to.value = prefill.to;
    form.to.dispatchEvent(new Event("input")); // покажет имя получателя
  }
  if (prefill.amount) form.amount.value = prefill.amount.replace(".", ",");
  //ключ один пока окно открыто, повтор после обрыва не спишет дважды
  transferKey = newKey();
}

$("#btn-transfer").addEventListener("click", () => openTransfer());

function showRecipient(text, kind = "") {
  const r = $("#recipient");
  r.textContent = text;
  r.className = "recipient " + kind;
}

function setTransferBy(by) {
  transferBy = by;
  document.querySelectorAll("#transfer-form .tab").forEach((t) => t.classList.toggle("active", t.dataset.by === by));
  const input = $("#transfer-form").to;
  $("#to-label").firstChild.textContent = by === "phone" ? "Телефон получателя " : "Номер счёта получателя ";
  input.value = "";
  input.placeholder = by === "phone" ? "+7 999 123-45-67" : "10 цифр";
  input.inputMode = by === "phone" ? "tel" : "numeric";
  showRecipient("");
  setTimeout(() => input.focus(), 0);
}

document.querySelectorAll("#transfer-form .tab").forEach((t) =>
  t.addEventListener("click", () => setTransferBy(t.dataset.by)));

// пока вводят номер, подсказываем кому уйдут деньги
let lookupTimer = null;
let lookupSeq = 0;
$("#transfer-form").to.addEventListener("input", () => {
  clearTimeout(lookupTimer);
  lookupSeq++;
  showRecipient("");
  const raw = $("#transfer-form").to.value;
  const digits = raw.replace(/\D/g, "");
  if (digits.length < (transferBy === "phone" ? 10 : 8)) return;

  lookupTimer = setTimeout(async () => {
    const seq = ++lookupSeq;
    const body = transferBy === "phone" ? { phone: raw } : { account: Number(digits) };
    try {
      const { data } = await api("/transfers/recipient", { method: "POST", body: JSON.stringify(body) });
      if (seq !== lookupSeq) return; // пока ждали,номер уже поменяли
      const who = data.name || "клиент RalWallet";
      showRecipient(`Получатель: ${who}, счёт №${data.account_id}, ${SIGN[data.currency] || data.currency}`, "ok");
    } catch (ex) {
      if (seq === lookupSeq) showRecipient(ex.message, "bad");
    }
  }, 400);
});

$("#transfer-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  busy(form, async (err) => {
    const from = Number(form.from.value);
    const digits = form.to.value.replace(/\D/g, "");
    const amount = parseAmount(form.amount.value);
    const body = { from_account_id: from, amount };

    if (transferBy === "phone") {
      if (digits.length < 10) return (err.textContent = "Введите телефон получателя");
      body.to_phone = form.to.value;
    } else {
      const to = Number(digits);
      if (!digits || to <= 0) return (err.textContent = "Введите номер счёта получателя");
      if (to === from) return (err.textContent = "Нельзя перевести на тот же счёт");
      body.to_account_id = to;
    }
    if (!amount) return (err.textContent = "Введите сумму, например 150 или 150,50");

    const src = accounts.find((a) => a.id === from);
    // счета могли устареть
    if (!src) return (err.textContent = "Счёт списания не найден, обновите страницу");
    const cur = src.currency;
    try {
      const { data, status } = await api("/transfers", {
        method: "POST",
        headers: { "Idempotency-Key": transferKey },
        body: JSON.stringify(body),
      });
      closeModal();
      transferKey = null;
      toast(status === 200
        ? "Этот перевод уже был выполнен раньше"
        : `Переведено ${money(amount, cur)} на счёт №${data.to_account_id}`);
      currentId = from;
      await loadAccounts();
    } catch (ex) {
      if (ex.status === 0) {
        err.textContent = "Нет связи. Нажмите «Перевести» ещё раз, деньги не спишутся дважды";
      } else {
        //сервер ответил значит перевода нет, новый ключ
        transferKey = newKey();
        err.textContent = ex.message;
      }
    }
  });
});

//пополнение
$("#btn-deposit").addEventListener("click", () => openModal("deposit-form"));

document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
  $("#deposit-form").amount.value = c.dataset.sum;
}));

$("#deposit-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  busy(form, async (err) => {
    const amount = parseAmount(form.amount.value);
    if (!amount) return (err.textContent = "Введите сумму, например 1000 или 99,90");
    try {
      const { data } = await api(`/accounts/${currentId}/deposit`, {
        method: "POST",
        body: JSON.stringify({ amount }),
      });
      closeModal();
      toast(`Счёт пополнен на ${money(amount, data.currency)}`);
      await loadAccounts();
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
});

// новый счёт
$("#open-new-account").addEventListener("click", () => openModal("account-form"));
$("#first-account").addEventListener("click", () => openModal("account-form"));

$("#account-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  busy(form, async (err) => {
    try {
      const { data } = await api("/accounts", {
        method: "POST",
        body: JSON.stringify({ currency: form.currency.value }),
      });
      closeModal();
      currentId = data.id;
      toast(`Открыт счёт №${data.id}`);
      // первый счёт сервер делает основным
      me = (await api("/users/me")).data;
      await loadAccounts();
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
});

// ---------- профиль ----------

//через #profile, чтоб работала кнопка назад
function route() {
  if (!me || $("#main").hidden) return;
  const profile = location.hash === "#profile";
  $(".layout").hidden = profile;
  $("#profile").hidden = !profile;
  if (profile) loadProfile();
}

window.addEventListener("hashchange", route);
$("#open-profile").addEventListener("click", () => (location.hash = "#profile"));
$("#back-to-accounts").addEventListener("click", () => {
  history.pushState(null, "", location.pathname);
  route();
});

async function loadProfile() {
  const [u, stats, logins] = await Promise.all([
    api("/users/me"),
    api("/users/me/stats"),
    api("/users/me/logins"),
  ]);
  me = u.data;
  renderMe();

  const f = $("#profile-form");
  for (const name of ["last_name", "first_name", "middle_name", "birth_date", "phone"]) {
    f[name].value = me[name] || "";
  }
  f.querySelector(".form-error").textContent = "";

  const s = $("#settings-form");
  s.main_account_id.innerHTML = accounts.length
    ? accounts.map((a) => `<option value="${a.id}">№${a.id}, ${ACC_NAME[a.currency] || a.currency}</option>`).join("")
    : `<option value="">Счетов пока нет</option>`;
  s.main_account_id.value = me.main_account_id || "";
  s.main_account_id.disabled = !accounts.length;
  // "50000.00" -> "50 000"
  s.daily_limit.value = me.daily_limit ? money(me.daily_limit, "").replace(/,00\s*$/, "").trim() : "";
  s.querySelector(".form-error").textContent = "";

  $("#sec-email").textContent = me.email;
  renderStats(stats.data);
  renderLogins(logins.data);
}

function fullDate(iso, withTime = false) {
  const opts = { day: "numeric", month: "long", year: "numeric" };
  if (withTime) Object.assign(opts, { hour: "2-digit", minute: "2-digit" });
  return new Date(iso).toLocaleString("ru-RU", opts);
}

function renderStats(st) {
  let html = `
    <div class="stat-row"><span class="muted">Клиент с</span><span>${fullDate(st.registered_at)}</span></div>
    <div class="stat-row"><span class="muted">Открыто счетов</span><span>${st.accounts_count}</span></div>`;

  if (!st.month.length) {
    html += `<div class="stat-row"><span class="muted">В этом месяце</span><span>переводов не было</span></div>`;
  }
  for (const m of st.month) {
    html += `
      <div class="stat-row"><span class="muted">Переведено за месяц</span><span>${money(m.sent, m.currency)}</span></div>
      <div class="stat-row"><span class="muted">Получено за месяц</span><span class="stat-in">${money(m.received, m.currency)}</span></div>`;
  }
  $("#stats").innerHTML = html;
}

// только браузер и система
function deviceName(ua) {
  if (!ua) return "неизвестное устройство";
  const browser =
    /YaBrowser/.test(ua) ? "Яндекс Браузер" :
    /Edg\//.test(ua) ? "Edge" :
    /OPR\//.test(ua) ? "Opera" :
    /Firefox\//.test(ua) ? "Firefox" :
    /Chrome\//.test(ua) ? "Chrome" :
    /Safari\//.test(ua) ? "Safari" :
    /curl|python|httpx/i.test(ua) ? "скрипт" : "браузер";
  const os =
    /Windows/.test(ua) ? "Windows" :
    /Android/.test(ua) ? "Android" :
    /iPhone|iPad/.test(ua) ? "iOS" :
    /Mac OS/.test(ua) ? "macOS" :
    /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser}, ${os}` : browser;
}

function renderLogins(list) {
  if (!list.length) {
    $("#logins").innerHTML = `<div class="muted">Пока пусто</div>`;
    return;
  }
  $("#logins").innerHTML = list.map((l) => `
    <div class="login-row">
      <span class="${l.success ? "login-ok" : "login-bad"}">${l.success ? "Вход" : "Неверный пароль"}</span>
      <span class="muted">${fullDate(l.created_at, true)}</span>
      <span class="muted small">${esc(deviceName(l.user_agent))}</span>
      <span class="muted small">${esc(l.ip || "")}</span>
    </div>`).join("");
}

$("#profile-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target;
  busy(f, async (err) => {
    const body = {
      last_name: f.last_name.value.trim(),
      first_name: f.first_name.value.trim(),
      middle_name: orNull(f.middle_name.value),
      birth_date: f.birth_date.value || null,
      phone: orNull(f.phone.value),
    };
    if (!body.last_name || !body.first_name) return (err.textContent = "Фамилия и имя обязательны");
    if (!body.birth_date) return (err.textContent = "Укажите дату рождения");
    try {
      const { data } = await api("/users/me", { method: "PATCH", body: JSON.stringify(body) });
      me = data;
      renderMe();
      f.phone.value = me.phone || ""; //сервер вернёт телефон в едином виде
      toast("Данные сохранены");
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
});

$("#settings-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target;
  busy(f, async (err) => {
    const body = {};
    if (accounts.length) body.main_account_id = Number(f.main_account_id.value);

    if (f.daily_limit.value.trim() === "") {
      body.daily_limit = null;
    } else {
      body.daily_limit = parseAmount(f.daily_limit.value);
      if (!body.daily_limit) return (err.textContent = "Лимит: введите сумму или оставьте поле пустым");
    }
    try {
      const { data } = await api("/users/me", { method: "PATCH", body: JSON.stringify(body) });
      me = data;
      renderAccounts();
      toast("Настройки сохранены");
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
});

$("#btn-password").addEventListener("click", () => openModal("password-form"));

$("#password-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target;
  busy(f, async (err) => {
    if (!f.current.value) return (err.textContent = "Введите текущий пароль");
    if (f.new1.value.length < 8) return (err.textContent = "Новый пароль должен быть не короче 8 символов");
    if (f.new1.value !== f.new2.value) return (err.textContent = "Пароли не совпадают");
    try {
      const { data } = await api("/users/me/password", {
        method: "POST",
        body: JSON.stringify({ current_password: f.current.value, new_password: f.new1.value }),
      });
      //старый токен уже всё
      token = data.access_token;
      save("token", token);
      closeModal();
      toast("Пароль изменён, на других устройствах выполнен выход");
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
});

$("#btn-email").addEventListener("click", () => openModal("email-form"));

$("#email-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target;
  busy(f, async (err) => {
    if (!f.email.value.trim() || !f.password.value) return (err.textContent = "Заполните оба поля");
    try {
      const { data } = await api("/users/me/email", {
        method: "POST",
        body: JSON.stringify({ new_email: f.email.value.trim(), password: f.password.value }),
      });
      me = data;
      renderMe();
      $("#sec-email").textContent = me.email;
      closeModal();
      toast("Email изменён");
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
});

//---------- всплывашка ----------

let toastTimer = null;
function toast(text, bad = false) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.toggle("bad", bad);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3500);
}

//---------- старт ----------

if (token) {
  loadMain().catch(() => logout());
} else {
  showAuth();
}
