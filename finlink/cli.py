def _drivers(root: Path, driver_override: str | None = None) -> tuple[object, object | None]:
    """Resolve market-data + news drivers. akshare is the default (no API key).

    `mock` runs fully offline for testing. alphavantage is deprecated but kept
    for backwards compatibility (requires API key; HK/SE support unreliable).
    """
    from finlink.config import RuntimeConfig
    from finlink.ingest.akshare_driver import AkshareDriver
    from finlink.ingest.alphavantage_driver import AlphaVantageDriver
    from finlink.ingest.mock import MockMarketDriver, MockNewsDriver
    from finlink.ingest.rss_news_driver import RSSNewsDriver

    cfg = RuntimeConfig.load(root)
    name = (driver_override or cfg.driver or "akshare").lower()
    
    if name == "mock":
        # strict: unknown tickers fail instead of silently inventing prices
        return MockMarketDriver(strict=True), MockNewsDriver()
    if name == "akshare":
        return AkshareDriver(), RSSNewsDriver()
    if name == "alphavantage":
        try:
            keys = [k for k in (cfg.alphavantage_api_key, cfg.alphavantage_api_key_2) if k]
            return AlphaVantageDriver(keys), RSSNewsDriver()
        except Exception as e:  # noqa: BLE001 - surface missing-key cleanly
            raise click.ClickException(str(e)) from e
    # openrouter / echo / openai etc. are LLM driver names; for market data they
    # all resolve to the default akshare driver.
    return AkshareDriver(), RSSNewsDriver()
