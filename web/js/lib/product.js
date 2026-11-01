/**
 * WP RUNNER-NAME-1: the product's name, in one place.
 *
 * The labels that name the server's own prefill downloads read it (the
 * Downloads "Loading now" row and mini line, the tile details and tooltips
 * that mention them, the Clients runner card's title), so it is clear who
 * loads. The rename to GameHangar (roadmap D18) changes this constant for
 * those labels; every other product string (page title, sidebar brand,
 * hints, links) is D18's own inventory and does not read it yet.
 *
 * Android twin: `PRODUCT_NAME` in `app/.../Product.kt`, pinned to this
 * literal by `ProductNameWebTwinTest`.
 */
export const PRODUCT_NAME = "SteamHangar";
