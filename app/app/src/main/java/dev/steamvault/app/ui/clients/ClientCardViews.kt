package dev.steamvault.app.ui.clients

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.steamvault.app.R
import dev.steamvault.app.ui.clients.logic.AGENT_OUTDATED_PILL
import dev.steamvault.app.ui.clients.logic.BYPASS_HINT_SUMMARY
import dev.steamvault.app.ui.clients.logic.CardPill
import dev.steamvault.app.ui.clients.logic.CardStat
import dev.steamvault.app.ui.clients.logic.ClientCardView
import dev.steamvault.app.ui.clients.logic.ClientKind
import dev.steamvault.app.ui.clients.logic.NO_STATS_TEXT
import dev.steamvault.app.ui.clients.logic.NowBox
import dev.steamvault.app.ui.clients.logic.OUTDATED_BOX_TITLE
import dev.steamvault.app.ui.clients.logic.PillLook
import dev.steamvault.app.ui.clients.logic.RECENT_HEADING
import dev.steamvault.app.ui.clients.logic.SummaryTile
import dev.steamvault.app.ui.clients.logic.UNREMOVABLE_SLASH_NOTE
import dev.steamvault.app.ui.clients.logic.moreChipText
import dev.steamvault.app.ui.clients.logic.outdatedAgentsPillText
import dev.steamvault.app.ui.theme.VaultColors

/*
 * The rc20 Clients card and summary tiles in Material terms (WP
 * ANDROID-REDESIGN-1a; web `components/client-card.js` and the tiles of
 * `views/clients.js`, design `docs/design/rc20/clients.html` and kit.html
 * "Clients card, three zones"). Draws `logic/ClientCard.kt`'s
 * [ClientCardView] / [SummaryTile] and computes nothing.
 *
 * The three zones stack in one column (web: below a 700px container,
 * always the case on a phone): identity (icon tile, name, mono meta line,
 * pills, outdated box, agent health row), activity (the "now" box and the
 * three figures), recent games as chips; footer with last-active text and
 * the actions. Colors are the kit tokens ([VaultColors]).
 */

private val CardShape = RoundedCornerShape(14.dp)
private val BoxShape = RoundedCornerShape(10.dp)
private val SmallButtonPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp)

/** Uppercase section label (kit `.sec` / `.k`). */
@Composable
internal fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text.uppercase(),
        style = MaterialTheme.typography.labelSmall.copy(letterSpacing = 0.8.sp, fontWeight = FontWeight.Medium),
        color = VaultColors.Dim2,
        modifier = modifier,
    )
}

/**
 * One device. The card merges its text, so TalkBack reads it as one item
 * in visual order; each button inside stays its own stop with its own
 * label ("Rename <name>", "Remove agent <id>"), and the remove error is
 * announced when it appears (web role=alert).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ClientCard(
    model: ClientCardView,
    onRename: (() -> Unit)?,
    onRemove: (() -> Unit)?,
) {
    Surface(
        color = VaultColors.Surface,
        shape = CardShape,
        border = BorderStroke(1.dp, if (model.outdatedText != null || model.health?.bypass == true) VaultColors.StatusStale.copy(alpha = 0.35f) else VaultColors.LineSoft),
        modifier = Modifier.fillMaxWidth().semantics(mergeDescendants = true) {},
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            // Zone 1: identity and health.
            Row(verticalAlignment = Alignment.CenterVertically) {
                KindIconTile(model.kind)
                Column(modifier = Modifier.weight(1f).padding(start = 12.dp)) {
                    Text(model.name, style = MaterialTheme.typography.titleMedium.copy(fontSize = 15.sp, fontWeight = FontWeight.Medium), color = VaultColors.Text)
                    Text(model.meta, style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace, fontSize = 11.5.sp), color = VaultColors.Dim2)
                }
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                StatusPill(model.pill)
                if (model.outdatedText != null) StatusPill(CardPill(AGENT_OUTDATED_PILL, PillLook.RUNNING), stale = true)
            }
            model.outdatedText?.let { OutdatedBox(it) }
            model.health?.let { AgentHealthRow(it.bypass, it.head, it.text) }

            // Zone 2: activity and numbers.
            NowBoxView(model.now)
            StatGrid(model.stats)

            // Zone 3: recent games.
            RecentZone(model)

            // Footer.
            CardFooter(model, onRename, onRemove)
        }
    }
}

/** The kind icon in its tile (web `.ico`): decoration, the meta line says what it is. */
@Composable
private fun KindIconTile(kind: ClientKind) {
    Box(
        contentAlignment = Alignment.Center,
        modifier = Modifier
            .size(38.dp)
            .clip(RoundedCornerShape(11.dp))
            .background(VaultColors.Raised)
            .border(1.dp, VaultColors.Line, RoundedCornerShape(11.dp))
            .clearAndSetSemantics {},
    ) {
        KindIcon(kind, VaultColors.Accent, 20.dp)
    }
}

/** web `CLIENT_ICONS`, coordinate for coordinate on a 24-unit grid. */
@Composable
private fun KindIcon(kind: ClientKind, color: Color, size: Dp) {
    Canvas(Modifier.size(size)) {
        val s = this.size.minDimension / 24f
        val stroke = Stroke(width = 1.8f * s, cap = StrokeCap.Round, join = StrokeJoin.Round)
        fun p(x: Float, y: Float) = Offset(x * s, y * s)
        when (kind) {
            ClientKind.AGENT -> {
                drawRoundRect(color, p(3f, 4f), Size(18f * s, 12f * s), CornerRadius(2f * s), style = stroke)
                drawLine(color, p(8f, 20f), p(16f, 20f), stroke.width, StrokeCap.Round)
                drawLine(color, p(12f, 16f), p(12f, 20f), stroke.width, StrokeCap.Round)
            }
            ClientKind.RUNNER -> {
                drawRoundRect(color, p(3f, 5f), Size(18f * s, 6f * s), CornerRadius(2f * s), style = stroke)
                drawRoundRect(color, p(3f, 13f), Size(18f * s, 6f * s), CornerRadius(2f * s), style = stroke)
            }
            ClientKind.ADDRESS -> {
                drawCircle(color, 9f * s, p(12f, 12f), style = stroke)
                drawLine(color, p(3f, 12f), p(21f, 12f), stroke.width, StrokeCap.Round)
                val path = Path().apply {
                    moveTo(12f * s, 3f * s)
                    cubicTo(15f * s, 6f * s, 15f * s, 18f * s, 12f * s, 21f * s)
                    moveTo(12f * s, 3f * s)
                    cubicTo(9f * s, 6f * s, 9f * s, 18f * s, 12f * s, 21f * s)
                }
                drawPath(path, color, style = stroke)
            }
        }
    }
}

private fun pillColor(look: PillLook): Color = when (look) {
    PillLook.RUNNING -> VaultColors.Accent
    PillLook.ONLINE -> VaultColors.StatusOk
    PillLook.OFFLINE -> VaultColors.StatusNone
    PillLook.UNKNOWN -> VaultColors.Dim2
}

/** Kit `.pill`: dot + word on a rounded chip; the dot is decoration. */
@Composable
private fun StatusPill(pill: CardPill, stale: Boolean = false) {
    val dot = if (stale) VaultColors.StatusStale else pillColor(pill.look)
    val text = when {
        stale -> VaultColors.StatusStale
        pill.look == PillLook.OFFLINE || pill.look == PillLook.UNKNOWN -> VaultColors.Dim
        else -> dot
    }
    val bg = when {
        stale -> VaultColors.StatusStale.copy(alpha = 0.12f)
        pill.look == PillLook.RUNNING -> VaultColors.AccentGlow
        else -> VaultColors.Raised
    }
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(bg).padding(start = 8.dp, end = 10.dp, top = 3.dp, bottom = 3.dp),
    ) {
        Box(Modifier.size(7.dp).background(dot, CircleShape))
        Text(pill.text, style = MaterialTheme.typography.labelMedium, color = text, modifier = Modifier.padding(start = 6.dp))
    }
}

/**
 * The outdated-agent box (web `.upd`). The web's "Show update command"
 * button opens Add a device, which the app does not have (no install
 * command on the phone), so the box states the versions only.
 */
@Composable
private fun OutdatedBox(text: String) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .clip(BoxShape)
            .background(VaultColors.StatusStale.copy(alpha = 0.08f))
            .border(1.dp, VaultColors.StatusStale.copy(alpha = 0.3f), BoxShape)
            .padding(horizontal = 12.dp, vertical = 10.dp),
    ) {
        Canvas(Modifier.size(18.dp).clearAndSetSemantics {}) {
            val s = size.minDimension / 24f
            val w = 2f * s
            drawLine(VaultColors.StatusStale, Offset(12f * s, 3f * s), Offset(12f * s, 15f * s), w, StrokeCap.Round)
            drawLine(VaultColors.StatusStale, Offset(8f * s, 11f * s), Offset(12f * s, 15f * s), w, StrokeCap.Round)
            drawLine(VaultColors.StatusStale, Offset(16f * s, 11f * s), Offset(12f * s, 15f * s), w, StrokeCap.Round)
            drawLine(VaultColors.StatusStale, Offset(4f * s, 19f * s), Offset(20f * s, 19f * s), w, StrokeCap.Round)
        }
        Column(modifier = Modifier.padding(start = 12.dp)) {
            Text(OUTDATED_BOX_TITLE, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium), color = VaultColors.Text)
            Text(text, style = MaterialTheme.typography.bodySmall, color = VaultColors.Dim)
        }
    }
}

/**
 * The agent health row (web `.agent`): check + "Healthy" + presence line, or
 * "Bypassing the cache" + the careful observation, with the explanation
 * behind a toggle (web: a closed `<details>`).
 */
@Composable
private fun AgentHealthRow(bypass: Boolean, head: String, text: String) {
    var open by rememberSaveable { mutableStateOf(false) }
    Column(
        modifier = Modifier.fillMaxWidth().clip(BoxShape).background(VaultColors.Bg).padding(horizontal = 12.dp, vertical = 9.dp),
    ) {
        Row {
            // kit.html draws "✓ Healthy"; the check is drawn, so it never
            // depends on the font having the glyph (web: an SVG).
            if (!bypass) {
                Canvas(Modifier.padding(top = 3.dp, end = 4.dp).size(12.dp).clearAndSetSemantics {}) {
                    val s = size.minDimension / 24f
                    val path = Path().apply {
                        moveTo(5f * s, 12.5f * s)
                        lineTo(10f * s, 17.5f * s)
                        lineTo(19f * s, 7f * s)
                    }
                    drawPath(path, VaultColors.StatusOk, style = Stroke(width = 3f * s, cap = StrokeCap.Round, join = StrokeJoin.Round))
                }
            }
            Text(
                buildAnnotatedString {
                    withStyle(SpanStyle(color = if (bypass) VaultColors.StatusStale else VaultColors.StatusOk, fontWeight = FontWeight.Medium)) {
                        append(head)
                    }
                    append("  ")
                    append(text)
                },
                style = MaterialTheme.typography.bodySmall,
                color = VaultColors.Dim,
            )
        }
        if (bypass) {
            TextButton(onClick = { open = !open }, contentPadding = PaddingValues(horizontal = 0.dp, vertical = 0.dp)) {
                Text(BYPASS_HINT_SUMMARY, style = MaterialTheme.typography.bodySmall)
            }
            if (open) {
                Text(stringResource(R.string.clients_bypass_explanation), style = MaterialTheme.typography.bodySmall, color = VaultColors.Dim)
            }
        }
    }
}

/** Zone 2's "now" box: raised with title, subline and speed while loading;
 * a dashed box with one line otherwise. */
@Composable
private fun NowBoxView(now: NowBox) {
    when (now) {
        is NowBox.Loading -> Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth().clip(BoxShape).background(VaultColors.Raised).padding(horizontal = 12.dp, vertical = 10.dp),
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(now.title, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium), color = VaultColors.Text)
                Text(now.sub, style = MaterialTheme.typography.bodySmall, color = VaultColors.Dim)
            }
            Text(
                now.speed,
                style = MaterialTheme.typography.bodyLarge.copy(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Medium, fontSize = 15.sp),
                color = VaultColors.Accent,
                modifier = Modifier.padding(start = 12.dp),
            )
        }
        is NowBox.Idle -> Box(
            modifier = Modifier
                .fillMaxWidth()
                .dashedBorder()
                .padding(horizontal = 12.dp, vertical = 10.dp),
        ) {
            Text(now.text, style = MaterialTheme.typography.bodyMedium, color = VaultColors.Dim)
        }
    }
}

/** Kit dashed outline (`border:1px dashed var(--line)`). */
private fun Modifier.dashedBorder(radius: Dp = 10.dp): Modifier = drawBehind {
    val w = 1.dp.toPx()
    drawRoundRect(
        color = VaultColors.Line,
        topLeft = Offset(w / 2, w / 2),
        size = Size(size.width - w, size.height - w),
        cornerRadius = CornerRadius(radius.toPx()),
        style = Stroke(width = w, pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 3.dp.toPx()))),
    )
}

/** Zone 2's three figures in one row (web `.stats`); the hit rate has a meter. */
@Composable
private fun StatGrid(stats: List<CardStat>) {
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
        for (stat in stats) StatCell(stat, Modifier.weight(1f))
    }
}

@Composable
private fun StatCell(stat: CardStat, modifier: Modifier = Modifier) {
    Column(modifier = modifier) {
        SectionLabel(stat.label)
        FigureText(stat.value, stat.unit, 17.sp)
        stat.meter?.let { Meter(it, Modifier.padding(top = 7.dp)) }
    }
}

/** `value<small>unit</small>`. */
@Composable
private fun FigureText(value: String, unit: String, size: TextUnit) {
    Text(
        buildAnnotatedString {
            append(value)
            if (unit.isNotEmpty()) {
                withStyle(SpanStyle(fontSize = size * 0.72f, color = VaultColors.Dim, fontWeight = FontWeight.Normal)) { append(" $unit") }
            }
        },
        style = MaterialTheme.typography.titleMedium.copy(fontSize = size, fontWeight = FontWeight.Medium),
        color = VaultColors.Text,
    )
}

/** Kit `.meter`: 4dp track, filled to [percent]; decoration (the figure says it). */
@Composable
private fun Meter(percent: Int, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().height(4.dp).clip(RoundedCornerShape(2.dp)).background(VaultColors.LineSoft)) {
        Box(Modifier.fillMaxWidth(percent.coerceIn(0, 100) / 100f).height(4.dp).clip(RoundedCornerShape(2.dp)).background(VaultColors.StatusOk))
    }
}

/** Zone 3: "Recently loaded" with game chips and "+N more" (web `.gchip`). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RecentZone(model: ClientCardView) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        SectionLabel(RECENT_HEADING)
        val recent = model.recent
        when {
            recent == null -> HintText(NO_STATS_TEXT)
            recent.chips.isEmpty() -> HintText(model.noRecent)
            else -> FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                for (chip in recent.chips) {
                    Text(
                        buildAnnotatedString {
                            append(chip.name)
                            chip.size?.let {
                                withStyle(SpanStyle(color = VaultColors.Dim2, fontFamily = FontFamily.Monospace, fontSize = 11.sp)) { append("  $it") }
                            }
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = VaultColors.Text,
                        modifier = Modifier.clip(RoundedCornerShape(8.dp)).background(VaultColors.Raised).padding(horizontal = 9.dp, vertical = 3.dp),
                    )
                }
                if (recent.more > 0) {
                    Text(
                        moreChipText(recent.more),
                        style = MaterialTheme.typography.bodySmall,
                        color = VaultColors.Dim,
                        modifier = Modifier.dashedBorder(8.dp).padding(horizontal = 9.dp, vertical = 3.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun HintText(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, color = VaultColors.Dim2)
}

/**
 * Footer (web `.cf`): last-active text (+ bytes not linked to a game) on
 * the left, Rename and Remove agent on the right in the same row -- no
 * band of its own under the text (ANDROID-GAPS-1 review nit). The
 * blocked-remove note and the remove error sit under it.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CardFooter(model: ClientCardView, onRename: (() -> Unit)?, onRemove: (() -> Unit)?) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        HorizontalDivider(color = VaultColors.LineSoft)
        FlowRow(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalArrangement = Arrangement.Center,
        ) {
            val info = if (model.unlinked != null) "${model.lastActive} · ${model.unlinked}" else model.lastActive
            Text(
                info,
                style = MaterialTheme.typography.bodySmall,
                color = VaultColors.Dim,
                modifier = Modifier.align(Alignment.CenterVertically).padding(end = 8.dp),
            )
            val showRename = model.rename && onRename != null
            val showRemove = model.removeId != null && onRemove != null
            if (showRename || showRemove) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (showRename) RenameButton(model.name, onRename!!)
                    if (showRemove) RemoveAgentButton(model.removeId!!, onRemove!!)
                }
            }
        }
        if (model.removeBlocked) HintText(UNREMOVABLE_SLASH_NOTE)
        if (!model.removeError.isNullOrEmpty()) {
            Text(
                text = model.removeError,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Assertive },
            )
        }
    }
}

/**
 * WP ANDROID-GAPS-1 (web D15-NAMES): the card's Rename action. A button is
 * its own TalkBack stop inside the merged card and reads "Rename <name>"
 * (web: the button text plus the card's name).
 */
@Composable
private fun RenameButton(name: String, onRename: () -> Unit) {
    val label = stringResource(R.string.clients_rename_button_cd, name)
    OutlinedButton(
        onClick = onRename,
        contentPadding = SmallButtonPadding,
        border = BorderStroke(1.dp, VaultColors.Line),
        modifier = Modifier.semantics { contentDescription = label },
    ) {
        Text(stringResource(R.string.clients_rename_button), style = MaterialTheme.typography.labelMedium, color = VaultColors.Text)
    }
}

/** WP ANDROID-REDESIGN-1a (web CLIENTS-4): "Remove agent" on the card,
 * TalkBack "Remove agent <id>"; opens the same confirm as the PCs sheet. */
@Composable
private fun RemoveAgentButton(clientId: String, onRemove: () -> Unit) {
    val label = stringResource(R.string.clients_remove_agent_button_cd, clientId)
    TextButton(onClick = onRemove, contentPadding = SmallButtonPadding, modifier = Modifier.semantics { contentDescription = label }) {
        Text(stringResource(R.string.clients_remove_agent_button), style = MaterialTheme.typography.labelMedium, color = VaultColors.Dim)
    }
}

/**
 * The page's four tiles (web `.sum`): two per row on a phone, four from
 * 600dp. Each tile reads as one TalkBack item; the outdated pill replaces
 * the Agents detail line when an agent is outdated.
 */
@Composable
internal fun SummaryTiles(tiles: List<SummaryTile>) {
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val perRow = if (maxWidth >= 600.dp) 4 else 2
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            for (row in tiles.chunked(perRow)) {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
                    for (tile in row) TileView(tile, Modifier.weight(1f))
                    repeat(perRow - row.size) { Box(Modifier.weight(1f)) }
                }
            }
        }
    }
}

@Composable
private fun TileView(tile: SummaryTile, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(12.dp))
            .background(VaultColors.Surface)
            .border(1.dp, if (tile.hi) VaultColors.StatusStale.copy(alpha = 0.35f) else VaultColors.LineSoft, RoundedCornerShape(12.dp))
            .padding(horizontal = 14.dp, vertical = 12.dp)
            .semantics(mergeDescendants = true) {},
    ) {
        SectionLabel(tile.label)
        FigureText(tile.value, tile.unit, 22.sp)
        tile.meter?.let { Meter(it, Modifier.padding(top = 6.dp)) }
        val outdated = tile.outdated
        if (outdated != null && outdated > 0) {
            Box(Modifier.padding(top = 4.dp)) { StatusPill(CardPill(outdatedAgentsPillText(outdated), PillLook.RUNNING), stale = true) }
        } else {
            Text(tile.detail, style = MaterialTheme.typography.bodySmall, color = VaultColors.Dim, modifier = Modifier.padding(top = 1.dp))
        }
    }
}
