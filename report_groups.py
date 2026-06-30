from __future__ import annotations

COMBINED_GROUP_NAME = "All"

THEME_REPORT_GROUPS: dict[str, list[str]] = {
    "风险指标": [
        "US.IWM",
        "US.UVXY",
    ],
    "超级平台": [
        "US.AAPL",
        "US.AMZN",
        "US.GOOGL",
        "US.META",
        "US.MSFT",
        "US.NVDA",
        "US.TSLA",
    ],
    "AI硬件": [
        "US.AMD",
        "US.APLD",
        "US.ASML",
        "US.AVGO",
        "US.CBRS",
        "US.COHR",
        "US.CRWV",
        "US.DELL",
        "US.DRAM",
        "US.EUV",
        "US.FOTO",
        "US.GFS",
        "US.GLW",
        "US.HPE",
        "US.INTC",
        "US.LITE",
        "US.MRVL",
        "US.NBIS",
        "US.NOK",
        "US.QCOM",
        "US.SMCI",
        "US.TSM",
    ],
    "电力能源": [
        "US.BE",
        "US.GEV",
        "US.OKLO",
        "US.VST",
    ],
    "AI时代软件": [
        "US.BBAI",
        "US.CRM",
        "US.CRWD",
        "US.DDOG",
        "US.DUOL",
        "US.FIG",
        "US.IBM",
        "US.IGV",
        "US.INTU",
        "US.MDB",
        "US.NET",
        "US.NOW",
        "US.ORCL",
        "US.PANW",
        "US.PLTR",
        "US.RDDT",
        "US.SAP",
        "US.SNOW",
        "US.SOUN",
        "US.TEAM",
        "US.TWLO",
    ],
    "加密与金融": [
        "US.COIN",
        "US.CRCL",
        "US.HOOD",
        "US.IBIT",
        "US.IREN",
        "US.MARA",
        "US.MSTR",
        "US.OPEN",
        "US.SOFI",
        "US.UPST",
    ],
    "中国科技": [
        "US.BABA",
        "US.FUTU",
        "US.GDS",
        "US.YINN",
    ],
}

STATIC_GROUP_ALIASES = {
    "risk": "风险指标",
    "platform": "超级平台",
    "ai-chip": "AI硬件",
    "ai-datacenter": "AI硬件",
    "ai-hardware": "AI硬件",
    "power": "电力能源",
    "ai-app": "AI时代软件",
    "enterprise-software": "AI时代软件",
    "ai-software": "AI时代软件",
    "software": "AI时代软件",
    "fintech": "加密与金融",
    "crypto": "加密与金融",
    "crypto-finance": "加密与金融",
    "china-tech": "中国科技",
}


class UnmappedReportSymbolsError(RuntimeError):
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols
        available = "、".join(THEME_REPORT_GROUPS)
        joined = ", ".join(symbols)
        super().__init__(
            "发现尚未确认主题分组的新标的，已在期权抓取和看板写入前中止。"
            f"未分组标的: {joined}。"
            f"请让 Codex 推断并向 Richard 确认以下分组之一: {available}。"
        )


def configured_theme_symbols() -> list[str]:
    return [
        symbol
        for symbols in THEME_REPORT_GROUPS.values()
        for symbol in symbols
    ]


def build_theme_report_groups(symbols: list[str]) -> dict[str, list[str]]:
    requested = sorted({
        str(symbol).strip().upper()
        for symbol in symbols
        if str(symbol).strip()
    })
    symbol_to_group: dict[str, str] = {}
    duplicates: list[str] = []
    for group_name, group_symbols in THEME_REPORT_GROUPS.items():
        for symbol in group_symbols:
            normalized = str(symbol).strip().upper()
            if normalized in symbol_to_group:
                duplicates.append(normalized)
            else:
                symbol_to_group[normalized] = group_name
    if duplicates:
        duplicate_text = ", ".join(sorted(set(duplicates)))
        raise RuntimeError(f"Theme configuration contains duplicate symbols: {duplicate_text}")

    unmapped = [symbol for symbol in requested if symbol not in symbol_to_group]
    if unmapped:
        raise UnmappedReportSymbolsError(unmapped)

    requested_set = set(requested)
    return {
        group_name: [
            symbol
            for symbol in group_symbols
            if symbol in requested_set
        ]
        for group_name, group_symbols in THEME_REPORT_GROUPS.items()
        if any(symbol in requested_set for symbol in group_symbols)
    }
