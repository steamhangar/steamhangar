package dev.steamvault.app.ui.downloads

import androidx.compose.foundation.BorderStroke
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.unit.dp
import dev.steamvault.app.ui.theme.VAULT_DISABLED_ALPHA
import dev.steamvault.app.ui.theme.vaultDangerButtonBorder
import dev.steamvault.app.ui.traffic.logic.HeroPart
import dev.steamvault.app.ui.traffic.logic.JOB_FIGURE_PATTERN
import dev.steamvault.app.ui.traffic.logic.VERIFY_SPEED_TEXT
import dev.steamvault.app.ui.traffic.logic.jobFigureParts
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * WP APP-DL-CARD-1 (operator report 2026-11-01, Android is the reported
 * surface): the job card's design pass, pinned against its web twin
 * (`web/js/components/job-row.js`, kit.css `.kit .job`, web test
 * `job-card-design.test.js`).
 *
 * - the figure pattern and `jobFigureParts` are the web's (literal);
 * - the card's padding, rhythm and note rule are the web's --sp values,
 *   px = dp (read from web/css, relative to the module directory `app/app`);
 * - Cancel's danger edge is the web `.kit .btn.danger` mix, as ARGB hex;
 * - the strong-generation wiring pins: each call's nearest enclosing
 *   function must be the intended composable ("deleted" vs "moved").
 *
 * When a web check fails, one of two edits applies: VALUE drift (the web
 * changed a value) -> change the Kotlin side and the literal here; GRAMMAR
 * drift (a rule moved or a shorthand split) -> widen the reader here.
 */
class JobCardDesignTwinTest {

    private fun read(path: String): String {
        val file = File(path)
        check(file.exists()) { "No file at ${file.absolutePath}: MOVED -> update this path; DELETED -> retire the pin deliberately." }
        return file.readText(Charsets.UTF_8)
    }

    private fun stripComments(text: String): String =
        text.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("(?m)^\\s*//.*$"), "")
            .replace(Regex("\\s//[^\"\\n]*$", RegexOption.MULTILINE), "")

    private val FUN = Regex("""(?:@Composable\s+)?(?:private\s+|internal\s+)?fun\s+(?:<[^>]+>\s+)?(?:\w+\.)?(\w+)""")

    private fun nearestEnclosingFun(code: String, callIdx: Int): String? =
        FUN.findAll(code).map { it.range.first to it.groupValues[1] }.filter { (start, _) -> start < callIdx }.maxByOrNull { (start, _) -> start }?.second

    private fun assertCalledFrom(path: String, call: String, expectedFun: String) {
        val code = stripComments(read(path))
        val idx = code.indexOf(call)
        assertTrue("$path: '$call' is DELETED -- the wiring is gone", idx >= 0)
        assertEquals("$path: '$call' MOVED -- its nearest enclosing function is no longer $expectedFun", expectedFun, nearestEnclosingFun(code, idx))
    }

    /** Body of the CSS rule whose selector is exactly [selector] (a line start or after `}`). */
    private fun rule(css: String, selector: String): String {
        val m = Regex("""(?:^|[}\n])\s*${Regex.escape(selector)}\s*\{([^}]*)\}""").find(css)
            ?: error("kit.css: no rule `$selector {` -- GRAMMAR drift? widen JobCardDesignTwinTest.rule")
        return m.groupValues[1]
    }

    private val base = "src/main/java/dev/steamvault/app"
    private val screen = "$base/ui/downloads/DownloadsScreen.kt"
    private val kitCss by lazy { stripCss(read("../../web/css/kit.css")) }
    private val themeCss by lazy { stripCss(read("../../web/css/theme.css")) }

    private fun stripCss(css: String): String = css.replace(Regex("/\\*.*?\\*/", RegexOption.DOT_MATCHES_ALL), "")

    /** The px value of a `--sp-N` token in theme.css. */
    private fun sp(n: Int): Int =
        Regex("""--sp-$n\s*:\s*(\d+)px""").find(themeCss)?.groupValues?.get(1)?.toInt()
            ?: error("theme.css: no --sp-$n token")

    // ---- the figure parts -------------------------------------------------

    @Test
    fun `the figure pattern is the web's, byte for byte`() {
        val js = read("../../web/js/lib/traffic-view.js")
        val decl = "export const JOB_FIGURE_PATTERN = \"$JOB_FIGURE_PATTERN\";"
        assertTrue("VALUE DRIFT: traffic-view.js no longer contains '$decl' -- update JOB_FIGURE_PATTERN in TrafficView.kt.", js.contains(decl))
        assertFalse("the pattern must stay backslash-free (a JS string and a Kotlin string alike)", JOB_FIGURE_PATTERN.contains('\\'))
        // The web walks the matches the same way (no other splitting rule).
        for (f in listOf(
            "for (const m of text.matchAll(new RegExp(JOB_FIGURE_PATTERN, \"g\"))) {",
            "parts.push({ b: m[0] });",
            "if (typeof text !== \"string\" || text === \"\") return [];",
        )) assertTrue("LOGIC DRIFT: traffic-view.js jobFigureParts no longer has `$f` -- port the change.", js.contains(f))
    }

    @Test
    fun `jobFigureParts bolds speeds, byte totals and durations like the web`() {
        fun b(t: String) = HeroPart(t, bold = true)
        fun p(t: String) = HeroPart(t)
        assertEquals(listOf(b("31.5 MB/s"), p(" · "), b("8.8 GB"), p(" loaded so far")), jobFigureParts("31.5 MB/s · 8.8 GB loaded so far"))
        assertEquals(listOf(b("0 MB/s"), p(" · "), b("0 GB"), p(" loaded so far")), jobFigureParts("0 MB/s · 0 GB loaded so far"))
        assertEquals(listOf(b("< 1 KB/s"), p(" · "), b("< 1 MB"), p(" loaded so far")), jobFigureParts("< 1 KB/s · < 1 MB loaded so far"))
        assertEquals(
            listOf(
                b("1.2 GB/s"), p(" · "), b("3.0 GB"), p(" of "), b("~51.6 GB"), p(" loaded so far (expected size) · about "),
                b("2 h"), p(" "), b("5 min"), p(" left"),
            ),
            jobFigureParts("1.2 GB/s · 3.0 GB of ~51.6 GB loaded so far (expected size) · about 2 h 5 min left"),
        )
        assertEquals(listOf(b("312 KB/s")), jobFigureParts("312 KB/s"))
        assertEquals(listOf(b("40.0 MB/s"), p(" · about "), b("2 h"), p(" left")), jobFigureParts("40.0 MB/s · about 2 h left"))
    }

    @Test
    fun `a line without a figure is one plain part, nothing is no part`() {
        assertEquals(listOf(HeroPart(VERIFY_SPEED_TEXT)), jobFigureParts(VERIFY_SPEED_TEXT))
        val shared = "Speed not measured: this game's depots are shared with other apps."
        assertEquals(listOf(HeroPart(shared)), jobFigureParts(shared))
        assertEquals(emptyList<HeroPart>(), jobFigureParts(""))
        assertEquals(emptyList<HeroPart>(), jobFigureParts(null))
        assertEquals(listOf(HeroPart("5 hosts, 3 minutes")), jobFigureParts("5 hosts, 3 minutes"))
    }

    // ---- spacing, rule and danger edge: web values, px = dp -----------------

    @Test
    fun `the card padding, rhythm and note rule are the web's --sp values`() {
        val card = rule(kitCss, ".kit .job")
        assertTrue("kit.css `.kit .job` padding is no longer var(--sp-3) var(--sp-4)", card.contains("padding:var(--sp-3) var(--sp-4);"))
        assertTrue("kit.css `.kit .job` gap is no longer var(--sp-3)", card.contains("gap:var(--sp-3);"))
        assertTrue("kit.css `.kit .job .t` gap is no longer var(--sp-3)", rule(kitCss, ".kit .job .t").contains("gap:var(--sp-3);"))
        assertEquals("JOB_CARD_PADDING_V = --sp-3", sp(3).dp, JOB_CARD_PADDING_V)
        assertEquals("JOB_CARD_PADDING_H = --sp-4", sp(4).dp, JOB_CARD_PADDING_H)
        assertEquals("JOB_CARD_GAP = --sp-3", sp(3).dp, JOB_CARD_GAP)
        val note = rule(kitCss, ".kit .job .holdnote, .kit .job .stopnote")
        assertTrue("kit.css note rule is no longer `border-left:2px solid var(--line)`", note.contains("border-left:2px solid var(--line);"))
        assertTrue("kit.css note inset is no longer var(--sp-2)", note.contains("padding-left:var(--sp-2);"))
        assertEquals(2.dp, JOB_NOTE_RULE)
        assertEquals("JOB_NOTE_INSET = --sp-2", sp(2).dp, JOB_NOTE_INSET)
        // The figures: bold like the Throughput caption (web `.m2 b` = `.hcap b`).
        assertEquals(rule(kitCss, ".kit .live .hcap b"), rule(kitCss, ".kit .job .m2 b"))
        assertTrue(rule(kitCss, ".kit .job .nm").contains("font-size:var(--fs-4); font-weight:500;"))
    }

    @Test
    fun `Cancel's edge is the web danger button mix`() {
        assertTrue(
            "kit.css `.kit .btn.danger` no longer mixes --danger 45 % into --line",
            rule(kitCss, ".kit .btn.danger").contains("border-color:color-mix(in srgb, var(--danger) 45%, var(--line));"),
        )
        assertTrue("the web Cancel is `ghost danger`", read("../../web/js/views/downloads.js").contains("actionButton(cancelling ? \"Cancelling…\" : \"Cancel\", \"ghost danger\""))
        fun argb(border: BorderStroke): Int {
            assertEquals(1.dp, border.width)
            return (border.brush as SolidColor).value.toArgb()
        }
        // danger #FF6F6F 45 % into line #26363B: (135.65, 79.65, 82.4) -> #885052.
        assertEquals(0xFF885052L.toInt(), argb(vaultDangerButtonBorder(enabled = true)))
        // Disabled: web `.btn:disabled{ opacity:.45 }`, alpha round(0.45 * 255) = 115 = 0x73.
        assertEquals(0x73885052L.toInt(), argb(vaultDangerButtonBorder(enabled = false)))
        assertEquals(0.45f, VAULT_DISABLED_ALPHA, 0f)
    }

    // ---- wiring: model to pixel ----------------------------------------------

    @Test
    fun `MUTATION PIN -- the card draws the shared pill, the bold figures and the web spacing`() {
        assertCalledFrom(screen, "StatusPill(HeroStatus(model.kind, model.statusWord))", "JobCard")
        assertCalledFrom(screen, "text = partsText(jobFigureParts(speedLine.text)),", "JobCard")
        assertCalledFrom(screen, "Modifier.padding(horizontal = JOB_CARD_PADDING_H, vertical = JOB_CARD_PADDING_V),", "JobCard")
        assertCalledFrom(screen, "verticalArrangement = Arrangement.spacedBy(JOB_CARD_GAP),", "JobCard")
        // The title block takes the free width and is a traversal group (TalkBack: name, meta, then the pill).
        assertCalledFrom(screen, "Column(modifier = Modifier.weight(1f).semantics { isTraversalGroup = true }) {", "JobCard")
        assertCalledFrom(screen, "horizontalArrangement = Arrangement.spacedBy(JOB_CARD_GAP),", "JobCard")
        assertCalledFrom(screen, "Text(model.name, style = jobCardTitleStyle())", "JobCard")
        assertCalledFrom(screen, "MaterialTheme.typography.titleMedium.copy(fontSize = 15.sp, fontWeight = FontWeight.Medium)", "jobCardTitleStyle")
        // Meta and figures in Dim (web `.meta` / `.m2{ color:var(--dim) }`).
        assertCalledFrom(screen, "style = JobCardMetaStyle,\n                        color = VaultColors.Dim,", "JobCard")
        assertCalledFrom(screen, "style = JobCardFigureStyle,\n                    color = VaultColors.Dim,", "JobCard")
        val code = stripComments(read(screen))
        val start = code.indexOf("internal fun JobCard(")
        assertTrue("JobCard is gone or no longer internal", start >= 0)
        val body = code.substring(start, code.indexOf("\nprivate fun ", start).let { if (it < 0) code.length else it })
        assertFalse("JobCard draws a bare StatusIcon again (the pill reads the word once)", body.contains("StatusIcon("))
        assertTrue("JobCard's buttons no longer wrap (FlowRow, web `.acts{ flex-wrap:wrap }`)", body.contains("FlowRow("))
        // The web card's order (components/job-row.js createJobCard, pinned by
        // web/tests/job-card-design.test.js): title + pill, speed line or hold
        // note, client note, stop note, actions.
        val order = listOf(
            "StatusPill(HeroStatus(", "if (speedLine != null) {", "text = holdNote ?: stringResource(",
            "if (clientNote != null) ClientNote(", "StopNote(pausingNote", "FlowRow(",
        ).map { it to body.indexOf(it) }
        for ((part, at) in order) assertTrue("JobCard lost `$part`", at >= 0)
        assertEquals("JobCard's blocks are no longer in the web order", order.sortedBy { it.second }.map { it.first }, order.map { it.first })
        val row = read("../../web/js/components/job-row.js")
        val webOrder = listOf("top.append(info, pill);", "speed.dataset.role = \"job-speed\";", "note.dataset.role = \"hold-note\";",
            "clientNote.dataset.role = \"client-note\";", "stopNote.dataset.role = \"stop-note\";", "acts.dataset.role = \"job-acts\";")
            .map { it to row.indexOf(it) }
        for ((part, at) in webOrder) assertTrue("job-row.js lost `$part`", at >= 0)
        assertEquals("job-row.js builds the card in another order -- re-check JobCard", webOrder.sortedBy { it.second }.map { it.first }, webOrder.map { it.first })
    }

    @Test
    fun `the title, meta and figure type are the web's --fs sizes, mono where the web is mono`() {
        fun fs(n: Int): Int = Regex("""--fs-$n\s*:\s*(\d+)px""").find(themeCss)?.groupValues?.get(1)?.toInt() ?: error("theme.css: no --fs-$n token")
        assertTrue(rule(kitCss, ".kit .job .nm").contains("font-size:var(--fs-4);"))
        assertTrue(rule(kitCss, ".kit .job .meta").contains("font:var(--fs-1) var(--mono); color:var(--dim);"))
        assertTrue(rule(kitCss, ".kit .job .m2").contains("font:var(--fs-2) var(--mono); color:var(--dim);"))
        val code = stripComments(read(screen))
        assertTrue("jobCardTitleStyle is not --fs-4 (${fs(4)} sp)", code.contains("titleMedium.copy(fontSize = ${fs(4)}.sp, fontWeight = FontWeight.Medium)"))
        assertTrue(
            "JobCardMetaStyle is not mono --fs-1 (${fs(1)} sp) regular",
            code.contains("private val JobCardMetaStyle = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Normal, fontSize = ${fs(1)}.sp)"),
        )
        assertTrue(
            "JobCardFigureStyle is not mono --fs-2 (${fs(2)} sp) regular",
            code.contains("private val JobCardFigureStyle = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Normal, fontSize = ${fs(2)}.sp)"),
        )
    }

    @Test
    fun `MUTATION PIN -- the stop note is Dim like the web hint, never the error colour`() {
        val code = stripComments(read(screen))
        val start = code.indexOf("private fun StopNote(")
        assertTrue("StopNote is gone", start >= 0)
        val body = code.substring(start, code.indexOf("\n}\n", start))
        assertTrue("StopNote is no longer onSurfaceVariant (Dim, web `.hint.stopnote`)", body.contains("color = MaterialTheme.colorScheme.onSurfaceVariant,"))
        assertFalse("StopNote is red again (colorScheme.error / StatusDanger)", body.contains("colorScheme.error") || body.contains("StatusDanger"))
        assertTrue("web `.hint` is --dim", rule(kitCss, ".kit .hint").contains("color:var(--dim);"))
    }

    @Test
    fun `MUTATION PIN -- Cancel is the danger button, the notes stand behind the rule, the queue note does not`() {
        assertCalledFrom(screen, "colors = vaultDangerButtonColors(),", "JobCard")
        assertCalledFrom(screen, "border = vaultDangerButtonBorder(enabled = cancelEnabled),", "JobCard")
        assertCalledFrom(screen, "ClientNote(clientNote, Modifier.fillMaxWidth().jobNoteRule())", "JobCard")
        assertCalledFrom(screen, "modifier = Modifier.fillMaxWidth().jobNoteRule(),\n                )", "JobCard")
        assertCalledFrom(screen, "modifier = Modifier.fillMaxWidth().jobNoteRule(),\n    )", "StopNote")
        assertCalledFrom(screen, "item(key = \"queue-hold-note\") { ClientNote(note) }", "DownloadsBody")
        assertCalledFrom(screen, "drawLine(VaultColors.Line, Offset(x, 0f), Offset(x, size.height), strokeWidth = w)", "jobNoteRule")
    }

    @Test
    fun `the web view writes the speed line through fillJobSpeed (twin call site)`() {
        val row = read("../../web/js/components/job-row.js")
        assertTrue("job-row.js lost fillJobSpeed", row.contains("export function fillJobSpeed(node, text) {"))
        assertTrue("fillJobSpeed no longer bolds through jobFigureParts", row.contains("for (const part of jobFigureParts(value)) {"))
        assertTrue("CALL-SITE DRIFT: views/downloads.js no longer calls fillJobSpeed(line, text)", read("../../web/js/views/downloads.js").contains("fillJobSpeed(line, text);"))
    }
}
