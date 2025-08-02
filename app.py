# --------------- unchanged imports / helpers above -----------------
# … keep everything until def scrape_dividends(code):

def scrape_dividends(code: str):
    """
    Returns (cash_dividends_in_current_FY , weighted_fran_pct)  or  (None, None).
    """

    def parse_table(html: str):
        soup = BeautifulSoup(html, "html.parser")

        # -------- locate dividend table ----------------------------
        target = None
        for tbl in soup.find_all("table"):
            hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            if any(h.startswith(("amount", "dividend")) for h in hdrs) and any(
                "frank" in h for h in hdrs
            ):
                target = tbl
                break
        if target is None:
            return None, None

        hdrs = [th.get_text(strip=True).lower() for th in target.find_all("th")]

        # ex-div header can be “ex-dividend date”, “ex-date”, etc.
        try:
            ex_i = next(i for i, h in enumerate(hdrs) if "ex" in h)
            amt_i = next(
                i for i, h in enumerate(hdrs) if h.startswith(("amount", "dividend"))
            )
            fr_i = next(i for i, h in enumerate(hdrs) if "frank" in h)
        except StopIteration:
            return None, None

        cutoff = FY_START
        tot_div = tot_fr_cash = 0.0

        for tr in target.find("tbody").find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) <= max(ex_i, amt_i, fr_i):
                continue

            exd = parse_exdate(tds[ex_i].get_text())
            if not exd or not (FY_START <= exd <= FY_END):
                continue

            try:
                amt = float(
                    re.sub(r"[^\d.,]", "", tds[amt_i].get_text()).replace(",", "")
                )
            except ValueError:
                continue
            try:
                fr_pct = float(re.sub(r"[^\d.]", "", tds[fr_i].get_text()))
            except ValueError:
                fr_pct = 0.0

            tot_div += amt
            tot_fr_cash += amt * fr_pct / 100.0

        if tot_div == 0:
            return None, None
        return round(tot_div, 6), round(tot_fr_cash * 100 / tot_div, 2)

    # ---------- source order unchanged (MarketIndex → InvestSMART) -
    # … keep the rest of scrape_dividends and the Flask routes exactly as before …
