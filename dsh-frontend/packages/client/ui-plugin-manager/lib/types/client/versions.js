import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
/**
 * A registry bundle's Versions section and its update dialog. The section summarizes the Host's version check in one
 * row and unfolds into the newer versions; the dialog confirms one update, follows the Host's steps, and reports how
 * the update ended, including the reinstallation of the removed version after a failed installation.
 */
import { useEffect, useState } from 'react';
import { Button, Checkbox, IconCheckOutlineRegular, IconChevronDownOutlineRegular, IconChevronRightOutlineRegular, IconCloseOutlineRegular, IconDownloadOutlineRegular, IconRefreshOutlineRegular, IconWarningOutlineRegular, Modal, StateDot, Tag, Tooltip, } from '@deepseek-ai/dsh-client-ui-primitives';
import { installable, updateCandidate } from "./manager-store.js";
import { managementText, registryText, restartText } from "./presentation.js";
import css from './PluginManagerPage.module.css';
/** Two digits of a date or time field. */
function pad(value) {
    return String(value).padStart(2, '0');
}
/** A version's local publication day as `YYYY-MM-DD`, which reads the same in every UI language. */
function publishedDate(version) {
    if (version.publishedAt === undefined)
        return undefined;
    const day = new Date(version.publishedAt);
    return `${String(day.getFullYear())}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}
/** The local time of day a check arrived, as 24-hour `HH:MM`. */
function checkedTime(checkedAt) {
    const time = new Date(checkedAt);
    return `${pad(time.getHours())}:${pad(time.getMinutes())}`;
}
/** The registry by name: npm's own and the mainland mirror by theirs, pnpm's own configuration as the default, any other by host. */
function registryName(registry, t) {
    return registryText(registry, t, null).name;
}
/** The DSH requirements an incompatible version states, as one phrase: `engines.dsh` or DSH peer ranges. */
function peersText(incompatible) {
    return Object.entries(incompatible.requirements).map(([name, range]) => `${name} ${range}`).join(', ');
}
const PROBLEM_KEYS = {
    'network': 'versionsProblemNetwork',
    'not-found': 'versionsProblemNotFound',
    'unknown': 'versionsProblemUnknown',
};
function Glyph({ tone, children }) {
    return _jsx("span", { className: css.versionGlyph, "data-tone": tone, "aria-hidden": "true", children: children });
}
function summaryOf(check, t) {
    if (check === undefined || check.phase === 'checking') {
        return { tone: 'neutral', glyph: _jsx(StateDot, { state: "ongoing", size: 14 }), title: t('versionsChecking'), newer: 0 };
    }
    const { list } = check;
    if (list.status === 'refused') {
        const failed = list.attempts.at(-1);
        return {
            tone: 'error', glyph: _jsx(IconCloseOutlineRegular, { size: 14 }),
            title: t(list.problem === 'network' ? 'versionsFailedNetwork' : 'versionsFailedOther'),
            // A check the transport lost asked no registry the page knows of; its words stand alone.
            sub: failed?.problem === undefined ? list.reason : `${registryName(failed.registry, t)} ${t(PROBLEM_KEYS[failed.problem])}`,
            newer: 0,
        };
    }
    const candidate = updateCandidate(list);
    if (candidate !== undefined) {
        const date = publishedDate(candidate);
        return {
            tone: 'info', glyph: _jsx(IconDownloadOutlineRegular, { size: 14 }), title: t('versionsAvailable', { version: candidate.version }),
            ...date === undefined ? {} : { sub: t('versionsPublished', { date }) }, newer: list.versions.length,
        };
    }
    if (list.versions.length === 0) {
        return { tone: 'success', glyph: _jsx(IconCheckOutlineRegular, { size: 14 }), title: t('versionsLatest'), sub: t('versionsCheckedAt', { time: checkedTime(check.checkedAt) }), newer: 0 };
    }
    const blocked = list.versions.find(version => !version.prerelease && version.incompatible !== undefined)?.incompatible;
    return blocked === undefined
        ? { tone: 'warn', glyph: _jsx(IconWarningOutlineRegular, { size: 14 }), title: t('versionsNoneInstallable'), sub: t('versionsNoneInstallableDetail'), newer: list.versions.length }
        : {
            tone: 'warn', glyph: _jsx(IconWarningOutlineRegular, { size: 14 }), title: t('versionsBlocked'), newer: list.versions.length,
            sub: t('versionsBlockedDetail', { version: blocked.version, peers: peersText(blocked), runtime: blocked.runtimeVersion }),
        };
}
/** One version row: its number and tags, why it cannot or should not be installed, its date, and its update action. */
function VersionRow({ version, installed, t, busy, onUpdate }) {
    if ('installedRow' in version) {
        return (_jsx("li", { className: css.versionRow, "data-plugin-version": version.version, children: _jsx("div", { className: css.versionMain, children: _jsxs("div", { className: css.versionTitle, children: [_jsx("span", { className: css.versionNumber, children: version.version }), _jsx(Tag, { className: css.statusTag, tone: "neutral", children: t('versionsInstalledTag') })] }) }) }));
    }
    const usable = installable(version);
    const note = version.incompatible !== undefined ? t('versionsNeedsDsh', { peers: peersText(version.incompatible) })
        : !version.bundle ? t('versionsNotBundle') : version.deprecated;
    const date = publishedDate(version);
    return (_jsxs("li", { className: css.versionRow, "data-plugin-version": version.version, ...usable ? {} : { 'data-blocked': '' }, children: [_jsxs("div", { className: css.versionMain, children: [_jsxs("div", { className: css.versionTitle, children: [_jsx("span", { className: css.versionNumber, children: version.version }), version.tags.map(tag => _jsx(Tag, { className: css.statusTag, tone: "outline", children: tag }, tag)), version.prerelease ? _jsx(Tag, { className: css.statusTag, tone: "outline", children: t('versionsPrereleaseTag') }) : null, version.deprecated === undefined ? null : _jsx(Tag, { className: css.statusTag, tone: "warning", children: t('versionsDeprecatedTag') })] }), note === undefined ? null : _jsx("span", { className: css.versionNote, "data-tone": usable && version.deprecated !== undefined ? 'warn' : undefined, children: note })] }), date === undefined ? null : _jsx("span", { className: css.versionDate, children: date }), _jsx("span", { className: css.versionAction, children: usable && version.version !== installed ? (_jsx(Button, { variant: version.deprecated === undefined && !version.prerelease ? 'outline' : 'ghost', size: "sm", disabled: busy, "aria-label": t('versionsUpdateLabel', { version: version.version }), onClick: () => { onUpdate(version.version); }, children: t('update') })) : null })] }));
}
/** The registries a refused check asked, each with what it answered. */
function AttemptRows({ attempts, t }) {
    return (_jsx("ul", { className: css.versionRows, children: attempts.map((attempt, index) => (_jsx("li", { className: css.versionRow, children: _jsxs("div", { className: css.versionMain, children: [_jsxs("div", { className: css.versionTitle, children: [_jsx("span", { className: css.versionNumber, children: registryName(attempt.registry, t) }), _jsx(Tag, { className: css.statusTag, tone: "outline", children: t(index === 0 ? 'versionsPrimaryTag' : 'versionsFallbackTag') }), attempt.problem === undefined ? null : _jsx(Tag, { className: css.statusTag, tone: "danger", children: t(PROBLEM_KEYS[attempt.problem]) })] }), attempt.reason === undefined ? null : _jsx("code", { className: css.versionLog, children: attempt.reason })] }) }, `${String(attempt.registry)}:${String(index)}`))) }));
}
/** The unfolded list: newer versions, the installed one, and where and when the list was read. */
function VersionList({ list, checkedAt, t, busy, onUpdate }) {
    const [prereleases, setPrereleases] = useState(false);
    const shown = list.versions.filter(version => prereleases || !version.prerelease);
    const fallback = list.attempts.length > 1 ? list.attempts[0] : undefined;
    return (_jsxs("div", { className: css.versionBody, children: [_jsxs("ul", { className: css.versionRows, children: [shown.map(version => (_jsx(VersionRow, { version: version, installed: list.installed, t: t, busy: busy, onUpdate: onUpdate }, version.version))), _jsx(VersionRow, { version: { version: list.installed, installedRow: true }, installed: list.installed, t: t, busy: busy, onUpdate: onUpdate })] }), list.omitted > 0 ? _jsx("p", { className: css.versionNote, children: t('versionsOmitted', { count: String(list.omitted) }) }) : null, _jsxs("div", { className: css.versionMeta, children: [_jsxs("dl", { className: css.versionFact, children: [_jsx("dt", { children: t('versionsRegistry') }), _jsxs("dd", { children: [registryName(list.registry, t), fallback === undefined ? null : (_jsx(Tooltip, { label: t('versionsFallbackHint', { registry: registryName(fallback.registry, t) }), side: "top", portal: true, children: _jsx("span", { className: css.versionHint, tabIndex: 0, children: _jsx(Tag, { className: css.statusTag, tone: "outline", children: t('versionsFallbackTag') }) }) }))] })] }), _jsxs("dl", { className: css.versionFact, children: [_jsx("dt", { children: t('versionsCheckedAtLabel') }), _jsx("dd", { children: checkedTime(checkedAt) })] }), _jsx("span", { className: css.versionSpacer }), list.versions.some(version => version.prerelease)
                        ? _jsx(Checkbox, { checked: prereleases, onChange: setPrereleases, label: t('versionsShowPrerelease') })
                        : null] })] }));
}
/**
 * The Versions section of an updatable bundle's page. It checks once when the page opens and folds the newer versions
 * behind a summary row.
 */
export function VersionSection({ pkg, check, t, busy, onEnsure, onCheck, onUpdate }) {
    const [open, setOpen] = useState(false);
    const { name, updatable } = pkg;
    useEffect(() => { if (updatable)
        onEnsure(name); }, [name, updatable, onEnsure]);
    if (!updatable)
        return null;
    const done = check?.phase === 'done' ? check : undefined;
    const summary = summaryOf(check, t);
    const refused = done?.list.status === 'refused' ? done.list : undefined;
    const listed = done?.list.status === 'listed' ? done.list : undefined;
    const unfolds = summary.newer > 0 || (refused !== undefined && refused.attempts.length > 0);
    const expanded = open && unfolds;
    const text = (_jsxs("span", { className: css.versionSummaryText, children: [_jsx("span", { className: css.versionSummaryTitle, children: summary.title }), summary.sub === undefined ? null : _jsx("span", { className: css.versionSummarySub, children: summary.sub })] }));
    return (_jsxs("section", { className: css.detailSection, "data-plugin-versions": true, children: [_jsxs("div", { className: css.sectionHead, children: [_jsx("h4", { className: css.sectionTitle, children: t('versionsTitle') }), _jsx("span", { className: css.versionSpacer }), refused === undefined ? (_jsx(Button, { variant: "ghost", size: "sm", icon: _jsx(IconRefreshOutlineRegular, { size: 13 }), disabled: check?.phase !== 'done', onClick: onCheck, children: t('versionsRecheck') })) : null] }), _jsxs("div", { className: css.versionPanel, children: [listed !== undefined && summary.newer > 0 ? (_jsxs("button", { type: "button", className: css.versionSummary, "aria-expanded": expanded, onClick: () => { setOpen(!open); }, children: [_jsx(Glyph, { tone: summary.tone, children: summary.glyph }), text, _jsxs("span", { className: css.versionSummaryEnd, children: [expanded ? t('versionsCollapse') : t('versionsNewerCount', { count: String(summary.newer) }), _jsx(IconChevronDownOutlineRegular, { className: css.detailsChevron, "aria-hidden": "true" })] })] })) : (_jsxs("div", { className: css.versionSummary, role: "status", children: [_jsx(Glyph, { tone: summary.tone, children: summary.glyph }), text, refused === undefined ? null : (_jsxs("span", { className: css.versionSummaryEnd, children: [unfolds ? (_jsxs("button", { type: "button", className: css.detailsToggle, "aria-expanded": expanded, onClick: () => { setOpen(!open); }, children: [t('versionsDetails'), _jsx(IconChevronDownOutlineRegular, { className: css.detailsChevron, "aria-hidden": "true" })] })) : null, _jsx(Button, { variant: "outline", size: "sm", onClick: onCheck, children: t('retry') })] }))] })), expanded && listed !== undefined && done !== undefined
                        ? _jsx(VersionList, { list: listed, checkedAt: done.checkedAt, t: t, busy: busy, onUpdate: onUpdate })
                        : null, expanded && refused !== undefined
                        ? _jsx("div", { className: css.versionBody, children: _jsx(AttemptRows, { attempts: refused.attempts, t: t }) })
                        : null] })] }));
}
/** The step labels of an update; the Host announces each step as it starts. */
function stepLabel(step, update, t) {
    switch (step) {
        case 'checking': return t('updateStepChecking', { version: update.to });
        case 'removing': return t('updateStepRemoving', { version: update.from });
        case 'installing': return t('updateStepInstalling', { version: update.to, registry: registryName(update.registry, t) });
        case 'restoring': return t('updateStepRestoring', { version: update.from });
    }
}
/** Whether a change result left its operation undone. */
function failed(application) {
    return application === 'failed' || application === 'cancelled';
}
/** The steps in the order the Host runs them. */
const STEP_ORDER = ['checking', 'removing', 'installing', 'restoring'];
/** The steps an answer implies, joined with the announced ones, so a lost announcement does not drop a step that ran. */
function settledSteps(announced, result) {
    const implied = new Set(['checking', ...announced]);
    if (result.step === 'remove')
        implied.add('removing');
    if (result.step === 'install')
        implied.add('installing');
    if (result.restore !== undefined)
        implied.add('restoring');
    return STEP_ORDER.filter(step => implied.has(step));
}
/** How one step ended, or that it still runs. */
function stepState(step, update, last) {
    const result = update.result;
    if (update.phase === 'running')
        return last ? 'running' : 'done';
    if (result === null)
        return last ? 'unknown' : 'done';
    switch (step) {
        case 'checking': return result.step === 'check' ? 'failed' : 'done';
        case 'removing': return result.step === 'remove' ? 'failed' : 'done';
        case 'installing': return failed(result.result.application) ? 'failed' : 'done';
        case 'restoring': return result.restore !== undefined && failed(result.restore.application) ? 'failed' : 'done';
    }
}
const STEP_GLYPHS = {
    running: _jsx(StateDot, { state: "ongoing", size: 12 }),
    done: _jsx(IconCheckOutlineRegular, { size: 12 }),
    failed: _jsx(IconCloseOutlineRegular, { size: 12 }),
    unknown: null,
};
/** What the update's outcome reads as: its glyph, title, and the sentence under it. */
function outcomeOf(update, title, t) {
    const error = { tone: 'error', glyph: _jsx(IconCloseOutlineRegular, { size: 14 }) };
    const result = update.result;
    // A finished update without the Host's answer carries the transport's words.
    if (result === null)
        return { ...error, title: t('updateLost'), detail: t('updateLostDetail', { reason: String(update.lost) }) };
    // A failed step always carries the Host's refusal; an absent one reads as the generic operation error.
    const reason = (change) => managementText({ code: 'operation-error', ...change.error, installing: true }, t);
    if (result.step === 'check')
        return { ...error, title: t('updateRefused', { version: update.to }), detail: reason(result.result) };
    if (result.step === 'remove')
        return { ...error, title: t('updateRemoveFailed', { version: update.from }), detail: reason(result.result) };
    if (!failed(result.result.application)) {
        return result.result.application === 'restart-required'
            ? { tone: 'warn', glyph: _jsx(IconWarningOutlineRegular, { size: 14 }), title: t('updateRestart', { version: update.to }), detail: restartText('restartNotice', result.result.restartReason, t) }
            : { tone: 'success', glyph: _jsx(IconCheckOutlineRegular, { size: 14 }), title: t('updateDone', { version: update.to }), detail: t('updateDoneDetail') };
    }
    // Without a reinstallation nothing was removed: the installation went over the running version and was rolled back.
    if (result.restore === undefined) {
        return { ...error, title: t('updateKept', { version: update.to }), detail: t('updateKeptDetail', { from: update.from, reason: reason(result.result) }) };
    }
    return failed(result.restore.application)
        ? { ...error, title: t('updateBroken'), detail: t('updateBrokenDetail', { from: update.from, to: update.to, name: title }) }
        : { ...error, title: t('updateRestored', { version: update.from }), detail: t('updateRestoredDetail', { from: update.from, to: update.to }) };
}
/** The update dialog: confirmation, then the Host's steps, then the outcome. */
export function UpdateDialog({ update, title, t, onRun, onClose, onToggleOutput }) {
    const running = update.phase === 'running';
    const steps = update.result === null ? update.steps : settledSteps(update.steps, update.result);
    const outcome = update.phase === 'done' ? outcomeOf(update, title, t) : undefined;
    // The failed run's own output: the target's installation, else the removal or the reinstallation.
    const output = [update.result?.result, update.result?.restore]
        .map(change => change !== undefined && failed(change.application) ? change.packageResult?.output : undefined)
        .find(text => text !== undefined && text !== '');
    return (_jsx(Modal, { open: true, onClose: () => { if (!running)
            onClose(); }, title: t('updateTitle', { name: title }), closeLabel: t('close'), className: css.updateDialog, footer: update.phase === 'confirm' ? (_jsxs(_Fragment, { children: [_jsx(Button, { variant: "outline", onClick: onClose, children: t('cancel') }), _jsx(Button, { variant: "primary", onClick: onRun, children: t('update') })] })) : running
            ? _jsx(Button, { variant: "outline", disabled: true, children: t('updateRunning') })
            : _jsx(Button, { variant: "primary", onClick: onClose, children: t('updateFinish') }), children: _jsx("div", { className: css.updateBody, "data-plugin-update": update.phase, children: update.phase === 'confirm' ? (_jsxs(_Fragment, { children: [_jsxs("div", { className: css.updateVersions, children: [_jsx("span", { className: css.versionNumber, "data-old": "", children: update.from }), _jsx(IconChevronRightOutlineRegular, { size: 14, "aria-hidden": "true" }), _jsx("span", { className: css.versionNumber, children: update.to })] }), _jsxs("dl", { className: css.versionFact, children: [_jsx("dt", { children: t('updateSource') }), _jsx("dd", { children: registryName(update.registry, t) })] }), _jsx("p", { className: css.updateText, children: t('updateDescription', { from: update.from, to: update.to }) })] })) : (_jsxs(_Fragment, { children: [outcome === undefined ? null : (_jsxs("div", { className: css.updateOutcome, role: "status", children: [_jsx(Glyph, { tone: outcome.tone, children: outcome.glyph }), _jsxs("span", { className: css.versionSummaryText, children: [_jsx("span", { className: css.versionSummaryTitle, children: outcome.title }), _jsx("span", { className: css.versionSummarySub, children: outcome.detail })] })] })), output === undefined ? null : (_jsxs(_Fragment, { children: [_jsxs("button", { type: "button", className: css.detailsToggle, "aria-expanded": update.outputOpen, onClick: onToggleOutput, children: [t('updateOutput'), _jsx(IconChevronDownOutlineRegular, { className: css.detailsChevron, "aria-hidden": "true" })] }), update.outputOpen ? _jsx("pre", { className: css.updateOutput, children: output }) : null] })), _jsx("ol", { className: css.updateSteps, children: steps.map((step, index) => {
                            const state = stepState(step, update, index === steps.length - 1);
                            return (_jsxs("li", { className: css.updateStep, "data-state": state, children: [_jsx("span", { className: css.updateStepDot, "aria-hidden": "true", children: STEP_GLYPHS[state] }), _jsx("span", { children: stepLabel(step, update, t) })] }, step));
                        }) })] })) }) }));
}
//# sourceMappingURL=versions.js.map