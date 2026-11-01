package dev.steamvault.app

/**
 * WP RUNNER-NAME-1: the product's name, in one place (web twin:
 * `web/js/lib/product.js` `PRODUCT_NAME`, pinned by
 * `ProductNameWebTwinTest`, which also checks `app_name`).
 *
 * The labels that name the server's own prefill downloads read it: the
 * Downloads "Loading now" runner row and the mini line, the tile details
 * that mentioned the runner, the Clients runner card's title. The rename to
 * GameHangar (roadmap D18) changes them by changing this constant; every
 * other product string (`app_name`, pairing and settings texts) is D18's
 * own inventory. A plain constant, not a string resource: the logic layer
 * that builds these labels is pure Kotlin without a Context.
 */
const val PRODUCT_NAME = "SteamHangar"
