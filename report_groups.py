from __future__ import annotations

PRIMARY_GROUP_NAME = "To be A8"
COMBINED_GROUP_NAME = "All"

LOW_LIQUIDITY_EXCLUDED_SYMBOLS = {
    "US.XYF",
    "US.AIFC",
    "US.REMX",
    "US.CLBT",
    "US.KC",
    "US.MANH",
    "US.FICO",
    "US.GWRE",
}

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
        "US.RDDT",
        "US.TSLA",
    ],
    "AI芯片": [
        "US.AMD",
        "US.ASML",
        "US.AVGO",
        "US.CBRS",
        "US.DRAM",
        "US.EUV",
        "US.GFS",
        "US.INTC",
        "US.MRVL",
        "US.NVDA",
        "US.QCOM",
        "US.TSM",
    ],
    "AI数据中心": [
        "US.APLD",
        "US.COHR",
        "US.CRWV",
        "US.DELL",
        "US.FOTO",
        "US.GLW",
        "US.HPE",
        "US.LITE",
        "US.NBIS",
        "US.NOK",
        "US.SMCI",
    ],
    "电力能源": [
        "US.BE",
        "US.GEV",
        "US.OKLO",
        "US.VST",
    ],
    "AI应用": [
        "US.BBAI",
        "US.CRWD",
        "US.DDOG",
        "US.DUOL",
        "US.FIG",
        "US.NET",
        "US.PANW",
        "US.PLTR",
        "US.SOUN",
        "US.TWLO",
    ],
    "企业软件": [
        "US.CRM",
        "US.IBM",
        "US.IGV",
        "US.INTU",
        "US.MDB",
        "US.MSFT",
        "US.NOW",
        "US.ORCL",
        "US.SAP",
        "US.SNOW",
        "US.TEAM",
    ],
    "金融科技": [
        "US.OPEN",
        "US.SOFI",
        "US.UPST",
    ],
    "加密资产": [
        "US.COIN",
        "US.CRCL",
        "US.HOOD",
        "US.IBIT",
        "US.IREN",
        "US.MARA",
        "US.MSTR",
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
    "ai-chip": "AI芯片",
    "ai-datacenter": "AI数据中心",
    "power": "电力能源",
    "ai-app": "AI应用",
    "enterprise-software": "企业软件",
    "fintech": "金融科技",
    "crypto": "加密资产",
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
