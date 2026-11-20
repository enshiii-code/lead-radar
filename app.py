"""Лид-радар: панель поиска клиентов для веб-разработчика (Streamlit, удобна на iPhone)."""
import io
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urlparse

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Лид-радар",
    page_icon="🔥",
    layout="centered",
    initial_sidebar_state="collapsed",
)

HOT, WARM, COLD = "🔥 Горячий", "🟠 Тёплый", "🟢 Холодный"
ORDER = {HOT: 0, WARM: 1, COLD: 2}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
SSL_MSG = "у сайта проблемы с SSL-сертификатом: браузер пугает посетителей предупреждением"

SOCIAL = (
    "vk.com", "vk.ru", "t.me", "telegram.me", "wa.me", "whatsapp.com", "taplink.cc",
    "taplink.ru", "instagram.com", "facebook.com", "ok.ru", "linktr.ee", "youtube.com",
    "avito.ru", "2gis.ru", "zoon.ru", "flamp.ru",
)
BUILDERS = (
    "tilda.ws", "tilda.cc", "wixsite.com", "wix.com", "business.site", "weebly.com",
    "jimdofree.com", "jimdosite.com", "blogspot.com", "wordpress.com", "ucoz.ru",
    "ucoz.net", "ucoz.com", "narod.ru", "setup.ru", "nethouse.ru", "mystrikingly.com",
    "godaddysites.com", "site123.me", "carrd.co",
)

COLS = ["Название", "Адрес", "Телефон", "Сайт", "Рейтинг", "Отзывы", "Карты"]
CANDIDATES = {
    "Название": ["name", "название", "название организации", "title", "company", "организация"],
    "Адрес": ["full_address", "address", "адрес"],
    "Телефон": ["phone", "телефон", "phones", "тел", "phone_1"],
    "Сайт": ["site", "website", "сайт", "web", "домен"],
    "Рейтинг": ["rating", "рейтинг", "reviews_rating", "stars"],
    "Отзывы": ["reviews", "отзывы", "количество отзывов", "reviews_count",
               "user_ratings_total", "reviews_number"],
    "Карты": ["location_link", "google_maps_url", "map_url", "maps_url", "place_url",
              "ссылка на карточку", "ссылка", "link", "url"],
}


# ---------- вспомогательные функции ----------
def clean_site(raw):
    if raw is None or (not isinstance(raw, str) and pd.isna(raw)):
        return ""
    s = str(raw).strip()
    if s.lower() in ("", "nan", "none", "null", "-", "—", "нет"):
        return ""
    return re.split(r"[\s,;|]+", s)[0]


def host_of(url):
    u = url if "://" in url else "https://" + url
    return (urlparse(u).hostname or "").lower().removeprefix("www.")


def host_matches(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def phone_digits(p):
    first = re.split(r"[,;|]", str(p))[0]
    d = re.sub(r"\D", "", first)
    if len(d) == 11 and d[0] in "78":
        return "7" + d[1:]
    if len(d) == 10:
        return "7" + d
    return d


def site_link(s):
    s = clean_site(s)
    return s if not s or "://" in s else "http://" + s


def fetch(url):
    try:
        return requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True), None
    except requests.exceptions.SSLError:
        return None, "ssl"
    except requests.exceptions.RequestException:
        return None, "fail"


def audit_site(raw):
    """Возвращает (статус, описание проблемы)."""
    url = clean_site(raw)
    if not url:
        return HOT, "у вас нет своего сайта"
    host = host_of(url)
    if host_matches(host, SOCIAL):
        return HOT, f"вместо сайта указана страница на {host}"
    if host_matches(host, BUILDERS):
        return WARM, f"сайт сделан на бесплатном поддомене конструктора ({host})"

    start = url if "://" in url else "https://" + url
    resp, err = fetch(start)
    ssl_bad = err == "ssl"
    if resp is None:
        resp, _ = fetch("http://" + url.split("://", 1)[-1])
    if resp is None:
        return WARM, SSL_MSG if ssl_bad else "сайт не открывается"

    problems = [SSL_MSG] if ssl_bad else []
    code = resp.status_code
    if code >= 400 and code not in (401, 403, 429):
        return WARM, f"сайт открывается с ошибкой {code}"

    final = resp.url
    if host_matches(host_of(final), SOCIAL):
        return HOT, f"вместо сайта указана страница на {host_of(final)}"
    if final.startswith("http://") and not ssl_bad:
        problems.append("сайт работает без защищённого протокола HTTPS")

    if code in (401, 403, 429):
        if problems:
            return WARM, " и ".join(problems)
        return COLD, "сайт закрыт от автопроверки, проверьте вручную"

    html = resp.text[:300000]
    if not re.search(r"<meta[^>]+name\s*=\s*[\"']?viewport", html, re.I):
        problems.append("сайт не адаптирован под телефоны")
    if problems:
        return WARM, " и ".join(problems)
    return COLD, "сайт в порядке"


def run_audit(df):
    cache = st.session_state.setdefault("site_cache", {})
    urls = list(dict.fromkeys(clean_site(s) for s in df["Сайт"]))
    todo = [u for u in urls if u not in cache]
    if todo:
        prog = st.progress(0.0, text="Проверяю сайты…")
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(audit_site, u): u for u in todo}
            for i, f in enumerate(as_completed(futs), 1):
                try:
                    cache[futs[f]] = f.result()
                except Exception:
                    cache[futs[f]] = (WARM, "сайт не удалось проверить")
                prog.progress(i / len(todo), text=f"Проверено {i} из {len(todo)}")
        prog.empty()
    out = df.copy()
    keys = out["Сайт"].map(clean_site)
    out["Статус"] = keys.map(lambda u: cache[u][0])
    out["Проблема"] = keys.map(lambda u: cache[u][1])
    return out


def read_table(f):
    if f.name.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(f)
    else:
        data = f.getvalue()
        for enc in ("utf-8-sig", "cp1251"):
            try:
                df = pd.read_csv(io.BytesIO(data), sep=None, engine="python", encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("не удалось определить кодировку CSV")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def guess(cols, names):
    low = {c.lower().strip(): c for c in cols}
    for n in names:
        if n in low:
            return low[n]
    for n in names:
        for lc, c in low.items():
            if n in lc:
                return c
    return None


def standardize(df, mapping):
    out = pd.DataFrame(index=df.index)
    for k in COLS:
        col = mapping.get(k)
        out[k] = df[col] if col in df.columns else ""
    for k in ("Название", "Адрес", "Телефон", "Сайт", "Карты"):
        out[k] = out[k].fillna("").astype(str).str.strip()
    out["Рейтинг"] = pd.to_numeric(
        out["Рейтинг"].astype(str).str.replace(",", ".").str.extract(r"(\d+\.?\d*)")[0],
        errors="coerce",
    )
    out["Отзывы"] = pd.to_numeric(
        out["Отзывы"].astype(str).str.replace(r"[^\d]", "", regex=True), errors="coerce"
    )
    out = out[out["Название"] != ""].reset_index(drop=True)
    return out


def to_excel(df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Лиды")
    return buf.getvalue()


# ---------- поиск по API ----------
def search_serpapi(query, key, pages):
    rows, ll = [], None
    for p in range(pages):
        params = {"engine": "google_maps", "q": query, "type": "search",
                  "hl": "ru", "gl": "ru", "api_key": key}
        if p > 0:
            params["start"] = p * 20
            if ll:
                params["ll"] = ll
        r = requests.get("https://serpapi.com/search.json", params=params, timeout=60)
        data = r.json()
        if "error" in data:
            raise RuntimeError(data["error"])
        items = data.get("local_results") or []
        if not items:
            break
        g = items[0].get("gps_coordinates")
        if ll is None and g:
            ll = f"@{g['latitude']},{g['longitude']},12z"
        for it in items:
            pid = it.get("place_id")
            rows.append({
                "Название": it.get("title"), "Адрес": it.get("address"),
                "Телефон": it.get("phone"), "Сайт": it.get("website"),
                "Рейтинг": it.get("rating"), "Отзывы": it.get("reviews"),
                "Карты": f"https://www.google.com/maps/place/?q=place_id:{pid}" if pid else "",
            })
    return rows


def search_places(query, key, pages):
    rows, token = [], None
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "places.displayName,places.rating,places.userRatingCount,"
        "places.websiteUri,places.nationalPhoneNumber,places.googleMapsUri,"
        "places.formattedAddress,nextPageToken",
    }
    for _ in range(pages):
        body = {"textQuery": query, "languageCode": "ru", "pageSize": 20}
        if token:
            body["pageToken"] = token
        r = requests.post("https://places.googleapis.com/v1/places:searchText",
                          json=body, headers=headers, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(r.text[:300])
        data = r.json()
        for pl in data.get("places", []):
            rows.append({
                "Название": (pl.get("displayName") or {}).get("text"),
                "Адрес": pl.get("formattedAddress"),
                "Телефон": pl.get("nationalPhoneNumber"),
                "Сайт": pl.get("websiteUri"),
                "Рейтинг": pl.get("rating"),
                "Отзывы": pl.get("userRatingCount"),
                "Карты": pl.get("googleMapsUri"),
            })
        token = data.get("nextPageToken")
        if not token:
            break
    return rows


# ---------- сообщение ----------
def build_message(row, sender):
    intro = (f"Здравствуйте! Меня зовут {sender}, я веб-разработчик." if sender
             else "Здравствуйте! Я веб-разработчик, делаю сайты для бизнеса в Москве и области.")
    name = row["Название"]
    if row["Статус"] == COLD:
        return (f"{intro} Посмотрел сайт «{name}» — выглядит прилично. Если хотите получать "
                "больше заявок (скорость, SEO, формы, аналитика), могу сделать бесплатный "
                "мини-аудит и прислать список улучшений. Интересно?")
    if pd.notna(row["Рейтинг"]) and pd.notna(row["Отзывы"]):
        rate = f"хорошие отзывы ({row['Рейтинг']:.1f}★, {int(row['Отзывы'])} шт.)"
    else:
        rate = "компания выглядит достойно"
    return (f"{intro} Нашёл «{name}» на картах — {rate}, но заметил: {row['Проблема']}. "
            "Из-за этого вы можете терять клиентов, которые сначала ищут компанию в "
            "интернете с телефона. Могу быстро сделать современный сайт с формой заявки и "
            "показать примеры работ. Интересно обсудить?")


# ---------- интерфейс ----------
st.title("🔥 Лид-радар")
st.caption("Компании без сайта или с плохим сайтом. Фильтры — в боковой панели "
           "(стрелка › слева вверху).")

try:
    secret_key = st.secrets.get("API_KEY", "")
except Exception:
    secret_key = ""

mode = st.radio("Источник данных", ["📂 Загрузить базу", "🔎 Поиск по API"], horizontal=True)

if mode.startswith("📂"):
    up = st.file_uploader("CSV или Excel (Outscraper, Parser.im и т. п.)",
                          type=["csv", "xlsx", "xls"])
    if up is not None:
        try:
            raw = read_table(up)
        except Exception as e:
            st.error(f"Не удалось прочитать файл: {e}")
            raw = None
        if raw is not None:
            with st.expander("Соответствие колонок (если определилось неверно)"):
                mapping = {}
                opts = ["— нет —"] + list(raw.columns)
                for k in COLS:
                    g = guess(raw.columns, CANDIDATES[k])
                    sel = st.selectbox(k, opts, index=opts.index(g) if g in opts else 0,
                                       key=f"map_{up.name}_{k}")
                    mapping[k] = None if sel == "— нет —" else sel
            if st.button("Загрузить в панель", type="primary"):
                st.session_state["base"] = standardize(raw, mapping)
                st.session_state.pop("leads", None)
else:
    provider = st.selectbox("Сервис", ["SerpApi (Google Maps)", "Google Places API (New)"])
    query = st.text_input("Ниша и район", placeholder="Детейлинг Москва")
    api_key = st.text_input("API-ключ", type="password", value=secret_key)
    pages = st.slider("Страниц выдачи (по 20 компаний)", 1, 3, 1)
    if st.button("Найти компании", type="primary"):
        if not query.strip() or not api_key.strip():
            st.warning("Введите запрос и API-ключ.")
        else:
            try:
                with st.spinner("Собираю компании…"):
                    fn = search_serpapi if provider.startswith("Serp") else search_places
                    rows = fn(query.strip(), api_key.strip(), pages)
                if not rows:
                    st.warning("Ничего не найдено.")
                else:
                    st.session_state["base"] = standardize(
                        pd.DataFrame(rows), {c: c for c in COLS})
                    st.session_state.pop("leads", None)
            except Exception as e:
                st.error(f"Ошибка поиска: {e}")

base = st.session_state.get("base")
if base is None:
    st.info("Загрузите файл или запустите поиск.")
    st.stop()

st.success(f"В базе {len(base)} компаний")
if st.button("🔍 Проверить сайты", type="primary"):
    st.session_state["leads"] = run_audit(base)

leads = st.session_state.get("leads")
if leads is None:
    st.stop()

# --- фильтры ---
with st.sidebar:
    st.header("Фильтры")
    min_rating = st.slider("Минимальный рейтинг", 0.0, 5.0, 4.0, 0.1)
    min_reviews = st.slider("Минимум отзывов", 0, 200, 10)
    show_unrated = st.checkbox("Показывать без рейтинга", value=False)
    statuses = st.multiselect("Статус лида", [HOT, WARM, COLD], default=[HOT, WARM])
    name_q = st.text_input("Поиск по названию")

pool = leads[
    ((leads["Рейтинг"] >= min_rating) | (show_unrated & leads["Рейтинг"].isna()))
    & (leads["Отзывы"].fillna(0) >= min_reviews)
].copy()
if name_q.strip():
    pool = pool[pool["Название"].str.contains(name_q.strip(), case=False, regex=False)]
pool["_o"] = pool["Статус"].map(ORDER)
pool = pool.sort_values(["_o", "Отзывы"], ascending=[True, False])
shown = pool[pool["Статус"].isin(statuses)]

c1, c2, c3 = st.columns(3)
c1.metric("🔥 Горячих", int((pool["Статус"] == HOT).sum()))
c2.metric("🟠 Тёплых", int((pool["Статус"] == WARM).sum()))
c3.metric("🟢 Холодных", int((pool["Статус"] == COLD).sum()))

# --- таблица ---
view = shown.copy()
view["Сайт"] = view["Сайт"].map(site_link).replace("", None)
view["WhatsApp"] = view["Телефон"].map(
    lambda p: f"https://wa.me/{phone_digits(p)}" if phone_digits(p) else None)
view["Карты"] = view["Карты"].replace("", None)
st.dataframe(
    view[["Статус", "Название", "Проблема", "Рейтинг", "Отзывы", "Телефон",
          "WhatsApp", "Сайт", "Карты"]],
    hide_index=True,
    column_config={
        "Рейтинг": st.column_config.NumberColumn(format="%.1f"),
        "Отзывы": st.column_config.NumberColumn(format="%d"),
        "WhatsApp": st.column_config.LinkColumn("WA", display_text="💬"),
        "Сайт": st.column_config.LinkColumn(display_text="🌐"),
        "Карты": st.column_config.LinkColumn(display_text="📍"),
    },
)

# --- выгрузка ---
export_cols = ["Статус", "Название", "Проблема", "Рейтинг", "Отзывы", "Телефон",
               "Сайт", "Адрес", "Карты"]
hot_df = pool[pool["Статус"] == HOT][export_cols]
st.download_button(
    f"⬇️ Скачать горячих лидов в Excel ({len(hot_df)})",
    data=to_excel(hot_df),
    file_name="hot_leads.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    disabled=hot_df.empty,
)
st.download_button(
    f"⬇️ Скачать всё по фильтру в Excel ({len(shown)})",
    data=to_excel(shown[export_cols]),
    file_name="leads_filtered.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    disabled=shown.empty,
)

# --- генератор сообщения ---
st.subheader("✉️ Первое сообщение")
if shown.empty:
    st.info("Нет компаний под текущие фильтры.")
    st.stop()

sender = st.text_input("Ваше имя (для подписи)", key="sender").strip()
idx = st.selectbox(
    "Компания", list(shown.index),
    format_func=lambda i: f"{shown.loc[i, 'Статус'][:1]} {shown.loc[i, 'Название']}",
)
row = shown.loc[idx]
st.caption(f"Проблема: {row['Проблема']}")
msg = st.text_area("Текст (можно править)", value=build_message(row, sender),
                   height=260, key=f"msg_{idx}_{sender}")

digits = phone_digits(row["Телефон"])
if digits:
    st.markdown(f"[📞 Позвонить: {row['Телефон']}](tel:+{digits})")
    st.link_button("💬 Открыть WhatsApp с текстом", f"https://wa.me/{digits}?text={quote(msg)}")
st.link_button("✈️ Отправить через Telegram", f"https://t.me/share/url?url=%20&text={quote(msg)}")
