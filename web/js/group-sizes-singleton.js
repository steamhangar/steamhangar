/**
 * The one group-size resolver for the whole page (WP TOOL-APPS-2c): the
 * Library grid and the detail sheet share its cached depot lists, so a
 * group's size is read once, not once per surface. See lib/group-sizes.js.
 */
import { api } from "./api.js";
import { createGroupSizeResolver } from "./lib/group-sizes.js";

export const groupSizes = createGroupSizeResolver({ fetchDetail: (appid) => api.game(appid) });
