package dev.steamvault.app.ui.settings.logic

/** Which unsaved-changes dots are on (see [settingsDirtyDots]). */
data class SettingsDirtyDots(
    /** Strip tabs whose section owns a key of the pending PATCH body. */
    val sections: Set<String>,
    /** The "Show expert settings" row: an expert key is in the body. */
    val expertSwitch: Boolean,
) {
    companion object {
        val NONE = SettingsDirtyDots(emptySet(), false)
    }
}

/**
 * WP ANDROID-POLISH-1: twin of web settings.js `paintDirtyDots`. A strip tab
 * shows the dot exactly while a key its section owns (its placed rows; for
 * "expert" every expert group's rows) is in [bodyKeys], the keys of the PATCH
 * body the drafts would send ([buildSettingsPatchDraft]), so a field typed
 * back to its saved value clears the dot again. The expert switch row owns
 * the same keys as the Expert panel (web `els.sectionKeys["expert-switch"]`),
 * so its dot shows while the panel is hidden too.
 */
fun settingsDirtyDots(placed: PlacedSettings?, bodyKeys: Set<String>): SettingsDirtyDots {
    if (placed == null || bodyKeys.isEmpty()) return SettingsDirtyDots.NONE
    val sections = LinkedHashSet<String>()
    for ((id, rows) in placed.sections) {
        if (rows.any { it.key in bodyKeys }) sections.add(id)
    }
    val expert = placed.expert.values.any { rows -> rows.any { it.key in bodyKeys } }
    if (expert) sections.add(EXPERT_SECTION_ID)
    return SettingsDirtyDots(sections, expert)
}

/** The Expert panel's id in [SettingsLayout.SECTIONS]. */
private const val EXPERT_SECTION_ID = "expert"
