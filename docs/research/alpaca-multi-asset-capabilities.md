# Alpaca multi-asset capability evidence

Status: non-authoritative research input. Checked 2026-08-23 against current official Alpaca
documentation only. “Options” records the US-listed option surface exposed through Alpaca, including
contradictory index-option evidence; the accepted identity seam remains limited to contracts with a
resolved equity or ETF underlying. Evidence is limited to Trading API, Market Data API, and directly
related asset references; Alpaca Broker API customer-account capabilities are outside scope.

This matrix records provider facts and contradictions used by
[the accepted architecture](../architecture.md#multi-asset-extension-constraint). It does not authorize
crypto or options, define investment policy, or override active repository documents.

References to account entitlements or state describe only Trading API facts for the operator's own
connected Alpaca account, as required by the comparison. They do not introduce an account domain,
account discovery, customer-account support, or multi-account orchestration.

## Capability matrix

| Dimension | US equities | Alpaca crypto spot | Alpaca-listed options | Official sources |
| --- | --- | --- | --- | --- |
| Instrument identity | The asset catalog exposes a UUID, `us_equity` class, exchange, symbol, status, and capability flags. Lookup accepts asset ID, symbol, or CUSIP. | The catalog exposes a UUID, `crypto` class, venue, and pair symbol such as `BTC/USD`; legacy unslashed symbols also remain accepted. Pair metadata includes minimum order size and quantity and price increments. | The options catalog exposes a separate contract UUID and symbol linked to `underlying_asset_id`, with root, underlying symbol, expiration, strike, call/put, style, `size`, status, tradability, and optional deliverables. One guide calls the offering equity options, while the data stream uses an SPXW index-option example. | [Assets](https://docs.alpaca.markets/us/reference/get-v2-assets-1), [asset lookup](https://docs.alpaca.markets/us/reference/get-v2-assets-symbol_or_asset_id), [crypto trading](https://docs.alpaca.markets/us/docs/crypto-trading), [option contracts](https://docs.alpaca.markets/us/reference/get-options-contracts), [options overview](https://docs.alpaca.markets/us/docs/options-trading-overview), [option stream](https://docs.alpaca.markets/us/docs/real-time-option-data) |
| Market-data feeds | IEX, SIP, delayed SIP, BOATS, and Alpaca-derived overnight feeds are distinct. Basic Trading API coverage is IEX; feed availability depends on entitlement. | Crypto data is separate and carries provider or location provenance; documented providers and locations can change. Some bars may derive from quote midpoints rather than trades. | Real-time data uses `indicative` or `opra` according to entitlement. The options stream is MessagePack-only. | [Market Data API](https://docs.alpaca.markets/us/docs/about-market-data-api), [stock stream](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data), [crypto stream](https://docs.alpaca.markets/us/docs/real-time-crypto-pricing-data), [option stream](https://docs.alpaca.markets/us/docs/real-time-option-data) |
| Trading clocks | Equities have exchange calendars plus regular, pre-market, after-hours, and eligible overnight sessions; calendars carry holidays and early closes. V0 intentionally uses the regular NYSE session. | Trading is documented as 24 hours every day. The current `/v3/clock` market-code list has no crypto market code. | OPRA has its own market clock. Options cannot use extended hours; an underlying may advertise `options_late_close` for 4:15 p.m. ET rather than 4:00 p.m., expiration adds separate deadlines, and current errors name special morning-expiration index-option cutoffs. | [Market clock](https://docs.alpaca.markets/us/reference/clock-1), [calendar](https://docs.alpaca.markets/us/reference/calendar-2), [24/5 equities](https://docs.alpaca.markets/us/docs/245-trading-for-trading-api), [crypto trading](https://docs.alpaca.markets/us/docs/crypto-trading), [assets](https://docs.alpaca.markets/us/reference/get-v2-assets-1), [options trading](https://docs.alpaca.markets/us/docs/options-trading), [options overview](https://docs.alpaca.markets/us/docs/options-trading-overview) |
| Quantities | `qty` means shares. Fractional eligibility depends on the asset and order form; `qty` and `notional` are mutually exclusive. V0 remains whole-share. | `qty` means base-asset units and may be fractional subject to pair-specific minimums and increments; `qty` and `notional` are mutually exclusive. | `qty` means whole contracts; fractional and notional option orders are rejected. Multi-leg parent quantity represents strategy units and each leg has a ratio. | [Create order](https://docs.alpaca.markets/us/reference/postorder), [fractional equities](https://docs.alpaca.markets/us/docs/fractional-trading), [crypto orders](https://docs.alpaca.markets/us/docs/crypto-orders), [options trading](https://docs.alpaca.markets/us/docs/options-trading), [level 3 options](https://docs.alpaca.markets/us/docs/options-level-3-trading) |
| Currencies | The Trading API account currency and documented US-equity notional are USD. | A pair has base and quote assets; current examples include USD, USDT, USDC, and crypto quote assets. Fees may be charged in the asset received. The direct order reference describes `notional` as dollars, leaving non-USD-pair notional semantics unclear. | Published contract and exercise examples are USD-based, while the documented contract shape does not carry a universal currency field. Underlying and cash effects must therefore retain explicit currency provenance. | [Account](https://docs.alpaca.markets/us/docs/account-plans), [create order](https://docs.alpaca.markets/us/reference/postorder), [crypto trading](https://docs.alpaca.markets/us/docs/crypto-trading), [crypto fees](https://docs.alpaca.markets/us/docs/crypto-fees), [option contracts](https://docs.alpaca.markets/us/docs/options-trading) |
| Contract multiplier | Quantity already denotes shares; there is no derivative multiplier. | Spot quantity denotes base-asset units; there is no derivative multiplier, but base/quote conversion and increments remain material. | Contract `size` supplies the underlying multiplier, commonly 100 in examples. Optional deliverables mean the value must be read from the specific contract rather than hard-coded. | [Option contracts](https://docs.alpaca.markets/us/docs/options-trading), [option activities](https://docs.alpaca.markets/us/docs/non-trade-activities-for-option-events) |
| Positions | `/v2/positions` reports currently open positions; a closed position stops being queryable. Quantity is shares. | The same endpoint family carries crypto positions, but quantity is in the base asset and valuation follows pair/account semantics. | The same position model carries `us_option`; quantity denotes contracts and valuation depends on contract terms. | [Open positions](https://docs.alpaca.markets/us/reference/getallopenpositions), [position lookup](https://docs.alpaca.markets/us/reference/getopenposition-1), [options positions](https://docs.alpaca.markets/us/docs/options-trading) |
| Order forms | The common endpoint supports simple and advanced equity classes and market, limit, stop, stop-limit, and trailing-stop forms, subject to class and session restrictions. V0 uses simple day-limit orders only. | The documented forms are simple market, limit, and stop-limit orders. | The endpoint supports simple and multi-leg orders. The current order reference documents market and limit, while another current guide claims additional single-leg stop forms. | [Create order](https://docs.alpaca.markets/us/reference/postorder), [order guide](https://docs.alpaca.markets/us/docs/orders-at-alpaca), [crypto orders](https://docs.alpaca.markets/us/docs/crypto-orders), [options trading](https://docs.alpaca.markets/us/docs/options-trading) |
| Time in force | Values include `day`, `gtc`, `opg`, `cls`, `ioc`, and `fok`, subject to order-form restrictions. Extended-hours orders have a narrower limit plus `day` or `gtc` contract. | Values are `gtc` and `ioc`; the detailed matrix allows stop-limit only with `gtc`. | The current Create Order reference says `day`; other current official pages also claim `gtc`. This is unresolved documentation drift. | [Create order](https://docs.alpaca.markets/us/reference/postorder), [order/TIF matrix](https://docs.alpaca.markets/us/docs/orders-at-alpaca), [options trading](https://docs.alpaca.markets/us/docs/options-trading) |
| Account entitlements | Account and asset must be active and unblocked with adequate buying power; fractional and shorting capabilities add separate gates where used. | Jurisdiction and active crypto status are additional gates. Crypto is non-marginable and non-shortable and consumes non-marginable buying power. | Approved and active options trading level, options buying power or collateral, contract tradability, and strategy permission are separate gates. Paper enablement does not prove live authority. | [Account](https://docs.alpaca.markets/us/docs/account-plans), [crypto trading](https://docs.alpaca.markets/us/docs/crypto-trading), [options trading](https://docs.alpaca.markets/us/docs/options-trading), [account configuration](https://docs.alpaca.markets/us/reference/patchaccountconfig-1) |
| Exercise | Not applicable. | Not applicable. | The exercise endpoint acts on all available held contracts and rejects requests between market close and midnight. A separate DNE endpoint prevents default expiry exercise. | [Exercise](https://docs.alpaca.markets/us/reference/optionexercise), [do not exercise](https://docs.alpaca.markets/us/reference/optiondonotexercise) |
| Assignment | Not applicable as an instrument lifecycle, although corporate actions can alter an equity position. | Not applicable. | Short contracts can be assigned overnight. Assignment is not delivered through the Trading API WebSocket; Alpaca directs clients to poll activities. | [Options trading](https://docs.alpaca.markets/us/docs/options-trading), [option activities](https://docs.alpaca.markets/us/docs/non-trade-activities-for-option-events) |
| Expiration | The instrument has no contract expiry; orders may expire and corporate actions may replace or alter the instrument. | The spot pair has no contract expiry and trades continuously. | ITM contracts are generally auto-exercised absent DNE; OTM contracts expire. Alpaca may liquidate when collateral is insufficient. Expiry can remove the option and change the underlying position or cash. | [Options trading](https://docs.alpaca.markets/us/docs/options-trading), [options overview](https://docs.alpaca.markets/us/docs/options-trading-overview), [option activities](https://docs.alpaca.markets/us/docs/non-trade-activities-for-option-events) |
| Fills | `trade_updates` and `FILL` activities report full and partial fills. Event price can differ from cumulative average fill price. | The same event family uses crypto units; fees are independent `CFEE` or `FEE` activities and may use the credited asset. | The same fill family includes option and multi-leg order shapes. Exercise, assignment, and expiration are activities rather than ordinary fills. | [Trading stream](https://docs.alpaca.markets/us/docs/websocket-streaming), [account activities](https://docs.alpaca.markets/us/docs/account-activities), [crypto fees](https://docs.alpaca.markets/us/docs/crypto-fees), [option activities](https://docs.alpaca.markets/us/docs/non-trade-activities-for-option-events) |
| Reconciliation activities | Reconciliation needs paginated all-status orders, fills, open positions, cash/account state, fees, and corporate actions. A missing open position is not by itself a closing receipt. | Orders, fills, positions, account state, and separately posted crypto fees need an explicit continuous-market cutoff. | Orders and leg state, positions, cash, fills, exercise, assignment, expiration, and resulting underlying positions must converge. In paper, positions and balances may update immediately while option activities appear the next day. | [Orders](https://docs.alpaca.markets/us/reference/getallorders-1), [client-order lookup](https://docs.alpaca.markets/us/reference/getorderbyclientorderid), [activities](https://docs.alpaca.markets/us/reference/getaccountactivities-2), [positions](https://docs.alpaca.markets/us/reference/getallopenpositions), [options trading](https://docs.alpaca.markets/us/docs/options-trading) |

## Safety-relevant conflicts and unknowns

These differences are current first-party evidence, not values the core may resolve by choosing the
widest interpretation.

| Topic | Conflicting or incomplete official evidence | Safe design input |
| --- | --- | --- |
| Option order forms and TIF | [Create Order](https://docs.alpaca.markets/us/reference/postorder) says market/limit with `day`; [Options Trading](https://docs.alpaca.markets/us/docs/options-trading) also claims stop/stop-limit and `gtc`. | Pin an adapter capability profile from recorded contract evidence and reject an unverified combination. Options remain disabled in V0. |
| Fractional equity forms | [Fractional Trading](https://docs.alpaca.markets/us/docs/fractional-trading) claims market, limit, stop, and stop-limit support, while current order tables impose form-specific restrictions. | V0 remains whole-share day-limit. Any later form needs recorded boundary evidence. |
| Option capability field | The current [Assets API](https://docs.alpaca.markets/us/reference/get-v2-assets-1) documents `has_options`; [Options Trading](https://docs.alpaca.markets/us/docs/options-trading) refers to `options_enabled`. | Parse an explicitly versioned raw representation and reject absent or conflicting fields. |
| Exercise activity code | [Account Activities](https://docs.alpaca.markets/us/docs/account-activities) lists `OPXRC`; [Option Event Activities](https://docs.alpaca.markets/us/docs/non-trade-activities-for-option-events) uses `OPEXC` plus `OPTRD`. | Preserve the raw activity type and leave reconciliation incomplete until the observed representation is validated. |
| DNE workflow | A current [DNE endpoint](https://docs.alpaca.markets/us/reference/optiondonotexercise) exists, while parts of the options guide still direct users to support. | Treat DNE as a versioned adapter capability, not a timeless core enum. |
| Option style | Current contract filters admit `american` and `european`; older overview material described an American-only offering. | Retain each contract's style and apply style-specific rules; never infer style from the asset class. |
| Option underlying and expiry schedule | The [options overview](https://docs.alpaca.markets/us/docs/options-trading-overview) calls the initial offering equity options but also names morning-expiration index-option rules, while the [option stream](https://docs.alpaca.markets/us/docs/real-time-option-data) uses SPXW. The contract shape does not provide one universal settlement schedule. | The accepted seam supports only a contract whose underlying resolves to an equity or ETF identity. Index or unknown underlyings fail closed until a dedicated underlying, settlement, and schedule variant is approved. |
| Crypto non-USD notional | Non-USD pairs exist, while the direct order schema describes notional as dollars. | Keep notional unsupported for non-USD pairs until official or recorded contract evidence resolves the unit. |
| Client-order idempotency | Lookup by client ID is documented, but duplicate-ID scope, retention, and POST idempotency guarantees are not. | Persist intent and submitted-payload hash; after ambiguity, query and reconcile before any retry. |
| Delivery guarantees | WebSocket documentation does not promise exactly-once or gap-free replay, and exact cross-endpoint consistency windows are not stated. | Deduplicate raw observations and reconcile against REST; event silence never proves no financial transition. |
| Environment portability | Official docs do not promise matching UUIDs, entitlements, or behavior between paper and live. | Namespace source identity by environment and rediscover capabilities after environment change. |

## Lifecycle observations

### Equity daily cycle

- Basic market data is IEX rather than consolidated SIP, and the calendar supplies actual holidays and
  early closes. A weekday wall clock cannot establish an eligible Market Session.
- Client order IDs have a lookup endpoint, while response request IDs must be retained by the caller.
- Order replacement can race with a fill, and closed positions disappear from `/v2/positions`.

Sources: [market-data plans](https://docs.alpaca.markets/us/docs/about-market-data-api),
[calendar](https://docs.alpaca.markets/us/reference/calendar-2),
[client-order lookup](https://docs.alpaca.markets/us/reference/getorderbyclientorderid),
[request IDs](https://docs.alpaca.markets/us/docs/getting-started-with-trading-api),
[replace order](https://docs.alpaca.markets/us/reference/patchorderbyorderid-1), and
[positions](https://docs.alpaca.markets/us/reference/getallopenpositions).

### Continuous crypto cycle

- Trading is 24/7 but no crypto market clock is documented, so the consumer must define and pin its
  own deterministic decision-window identity and cutoff.
- Pair-specific quantities, data provider or location, quote-derived prices, and separately posted
  fee currency are material inputs. A fill cannot alone complete reconciliation.

Sources: [crypto trading](https://docs.alpaca.markets/us/docs/crypto-trading),
[market clock](https://docs.alpaca.markets/us/reference/clock-1),
[crypto stream](https://docs.alpaca.markets/us/docs/real-time-crypto-pricing-data),
[crypto snapshots](https://docs.alpaca.markets/us/reference/cryptosnapshots-1), and
[crypto fees](https://docs.alpaca.markets/us/docs/crypto-fees).

### Option expiration or assignment

- Contract-list defaults include only active contracts expiring before the next weekend and are
  paginated, so complete snapshots require explicit bounds and page exhaustion.
- Exercise is a separate operation; assignment is absent from Trading API WebSocket events. Contract
  removal and the paired underlying trade are distinct activities.
- Current first-party material exposes SPXW data and morning-expiration index-option rules despite
  describing the initial offering as equity options. An index contract cannot be treated as if it had
  an equity underlying or equity-session expiration schedule.
- Paper positions and balances can change before the corresponding option activity appears the next
  day. Stream silence or one position snapshot cannot establish completion.

Sources: [option contracts](https://docs.alpaca.markets/us/reference/get-options-contracts),
[exercise](https://docs.alpaca.markets/us/reference/optionexercise),
[options trading](https://docs.alpaca.markets/us/docs/options-trading), and
[option activities](https://docs.alpaca.markets/us/docs/non-trade-activities-for-option-events), plus
[the option stream](https://docs.alpaca.markets/us/docs/real-time-option-data) and
[options overview](https://docs.alpaca.markets/us/docs/options-trading-overview) for the index conflict.
