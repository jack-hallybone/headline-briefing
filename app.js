let allItems = [];
let sourceOrder = [];
let activeCategory = null;

// A virtual tab (not a real category) that shows the unread items from every
// category at once — for coming back later to see what you haven't read.
const UNREAD_TAB = 'Unread';

// Tiny DOM builder: el('div', { class: 'x', text: 'hi', role: 'tab' }, child, child).
// 'text' sets textContent (so feed data is always escaped); any other key becomes
// an attribute. Nullish children are skipped.
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else node.setAttribute(key, value);
  }
  node.append(...children.filter((child) => child != null));
  return node;
}

// --- Read tracking ----------------------------------------------------
// Remembers which headlines you've opened, in this browser only
// (localStorage). On each load we keep the stored ids that still appear in
// the current feed and drop the rest: a headline read at breakfast stays
// read at lunch while it's still in the feed, but the store can't grow
// without bound (it never holds more than one feed's worth of ids).
const READ_KEY = 'read-headlines';
let readIds = new Set();

function persistRead() {
  try {
    localStorage.setItem(READ_KEY, JSON.stringify([...readIds]));
  } catch { /* storage full or disabled; non-fatal */ }
}

function loadRead(items) {
  readIds = new Set();
  let stored = [];
  try {
    const raw = JSON.parse(localStorage.getItem(READ_KEY) || 'null');
    // Accept the current array form and the older { v, ids } object form.
    stored = (Array.isArray(raw) ? raw : (raw && raw.ids) || [])
      .filter((id) => typeof id === 'string');
  } catch { /* storage unavailable or corrupt; start fresh */ }
  // Don't reconcile against an empty feed (e.g. a failed load) — that would
  // needlessly wipe what you've read. Otherwise prune to the current feed.
  if (!items.length) return;
  const currentIds = new Set(items.map((i) => i.id));
  readIds = new Set(stored.filter((id) => currentIds.has(id)));
  persistRead();
}

function markRead(id) {
  if (!id || readIds.has(id)) return;
  readIds.add(id);
  persistRead();
}

function parseIso(iso) {
  const d = new Date(iso || '');
  return isNaN(d) ? null : d;
}

function formatRelativeTime(iso) {
  const then = parseIso(iso);
  if (!then) return '';
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return minutes + (minutes === 1 ? ' minute ago' : ' minutes ago');
  const hours = Math.round(minutes / 60);
  if (hours < 24) return hours + (hours === 1 ? ' hour ago' : ' hours ago');
  const days = Math.round(hours / 24);
  return days + (days === 1 ? ' day ago' : ' days ago');
}

function groupBySource(items) {
  const groups = new Map();
  for (const item of items) {
    const key = item.source || 'Other';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }

  const ordered = new Map();
  for (const source of sourceOrder) {
    if (groups.has(source)) {
      ordered.set(source, groups.get(source));
      groups.delete(source);
    }
  }
  for (const [source, items] of groups) {
    ordered.set(source, items);
  }
  return ordered;
}

function isSafeUrl(url) {
  try {
    return ['http:', 'https:'].includes(new URL(url).protocol);
  } catch {
    return false;
  }
}

function makeUrlDisplay(url) {
  return el('p', { class: 'url-display', text: isSafeUrl(url) ? url : 'Link unavailable' });
}

function renderItem(item) {
  const body = el('div', { class: 'item-body' });
  if (item.summary) body.append(el('p', { class: 'summary', text: item.summary }));
  body.append(makeUrlDisplay(item.link));

  const details = el('details', { class: 'item' },
    el('summary', {},
      el('div', { class: 'time-line', text: formatRelativeTime(item.published) || 'Undated' }),
      el('h3', { class: 'headline', text: item.title })),
    body);

  if (readIds.has(item.id)) details.classList.add('read');
  details.addEventListener('toggle', () => {
    if (!details.open) {
      markRead(item.id);
      details.classList.add('read');
    }
  });
  return details;
}

function renderTabs(categories) {
  const tabs = document.getElementById('tabs');
  tabs.innerHTML = '';
  // No tab bar for a single (or empty) category — it would just be clutter.
  if (categories.length <= 1) {
    tabs.style.display = 'none';
    return;
  }
  tabs.style.display = '';
  for (const category of categories) {
    const btn = el('button', {
      class: 'tab',
      type: 'button',
      role: 'tab',
      'aria-selected': String(category === activeCategory),
      text: category,
    });
    btn.addEventListener('click', () => {
      activeCategory = category;
      renderTabs(categories);
      renderItems();
    });
    tabs.append(btn);
  }
}

function renderItems() {
  const content = document.getElementById('content');
  content.innerHTML = '';
  const items = activeCategory === UNREAD_TAB
    ? allItems.filter((i) => !readIds.has(i.id))
    : allItems.filter((i) => (i.category || 'Other') === activeCategory);
  if (!items.length) {
    const message = activeCategory === UNREAD_TAB
      ? "No unread headlines — you're all caught up."
      : 'No headlines in this category right now.';
    content.append(el('p', { class: 'empty-state', text: message }));
    return;
  }
  for (const [source, sourceItems] of groupBySource(items)) {
    const section = el('section', { class: 'group' }, el('h2', { class: 'source-label', text: source }));
    for (const item of sourceItems) section.append(renderItem(item));
    content.append(section);
  }
}

function showEmpty(message) {
  const tabs = document.getElementById('tabs');
  tabs.innerHTML = '';
  tabs.style.display = 'none';
  const content = document.getElementById('content');
  content.innerHTML = '';
  content.append(el('p', { class: 'empty-state', text: message }));
}

function formatBuildTime(iso) {
  const d = parseIso(iso);
  return d ? d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : '';
}

function renderBuildInfo(data) {
  const box = document.getElementById('build-info');
  if (!box) return;
  const stamp = formatBuildTime(data.generated_at || '');
  // Only a real hex commit hash is shown (defence-in-depth on feed data).
  const commit = /^[0-9a-f]{7,40}$/i.test(data.commit || '') ? data.commit : '';
  const parts = [stamp, commit].filter(Boolean);
  box.textContent = parts.length ? 'v ' + parts.join(' | ') : '';
}

function render(data) {
  allItems = data.items || [];
  sourceOrder = data.sources || [];
  loadRead(allItems);
  renderBuildInfo(data);

  const sourceCount = new Set(allItems.map((i) => i.source)).size;
  const generated = formatRelativeTime(data.generated_at);
  document.getElementById('meta').textContent =
    (generated ? 'Updated ' + generated + ' · ' : '') +
    allItems.length + ' headlines from ' + sourceCount + ' sources';

  if (data.errors && data.errors.length) {
    document.getElementById('errors').textContent = 'Some feeds did not load: ' + data.errors.join('; ');
  }

  if (!allItems.length) {
    showEmpty('No headlines available right now.');
    return;
  }

  const categories = (data.categories && data.categories.length)
    ? data.categories
    : [...new Set(allItems.map((i) => i.category || 'Other'))]
      .sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  activeCategory = categories[0];  // default landing stays the first category
  // Prepend an "Unread" tab when there's a tab bar to add it to (more than one
  // real category). It's leftmost but not the default view.
  const tabList = categories.length > 1 ? [UNREAD_TAB, ...categories] : categories;
  renderTabs(tabList);
  renderItems();
}

fetch('data/data.json', { cache: 'no-store' })
  .then((res) => {
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  })
  .then(render)
  .catch((err) => {
    document.getElementById('meta').textContent = '';
    showEmpty('Could not load headlines (' + err.message + ').');
  });
