import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/** Cross-session retained reminders with local search and selection. */
import { useEffect, useMemo, useRef, useState } from 'react';
import clsx from 'clsx';
import { Button, IconClockOutlineRegular, IconCloseOutlineRegular, IconEllipsisOutlineRegular, IconPlusOutlineRegular, IconSearchOutlineRegular, IconTrashOutlineRegular, Input, Menu, Modal, Tooltip, } from '@deepseek-ai/dsh-client-ui-primitives';
import { CatalogFeedback } from "./CatalogFeedback.js";
import { TaskDetail, useTaskDetail } from "./TaskDetail.js";
import { useRelativeClock } from "./relative-clock.js";
import { formatScheduleFrequency, nextRunParts, taskName, zoneLabel } from "./schedule-format.js";
import { ScheduleEmptyIllustration } from "./ScheduleEmptyIllustration.js";
import css from './TaskManagerPage.module.css';
/**
 * Render retained tasks with authoritative deletion and timing-only edits.
 * @param props - framework catalog snapshot, localized copy, and action callbacks.
 * @returns the searchable task list beside the selected task's detail.
 */
export function TaskManagerPage(props) {
    const { useCatalog, onNewTask, onClearEnded, onRetry, t } = props;
    const catalog = useCatalog(snapshot => snapshot);
    const { records, status } = catalog;
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('active');
    const [selectedId, setSelectedId] = useState(null);
    const [clearConfirm, setClearConfirm] = useState(false);
    const [clearMenu, setClearMenu] = useState(false);
    const [clearing, setClearing] = useState(false);
    // The rows state how long remains, so they read the shared ticking clock
    // rather than a value sampled at mount: a catalog refresh can move the target
    // this text describes.
    const now = useRelativeClock();
    const detail = useTaskDetail(props, catalog, selectedId ?? undefined);
    const { record: catalogRecord, task: selected, id: detailId, confirmId, setConfirmId, setTab } = detail;
    const rowRef = useRef(null);
    const headingRef = useRef(null);
    const clearTriggerRef = useRef(null);
    // Set when the confirmation opens: its settled removal can hide the trigger
    // before the Modal returns focus, and so can another client's cleanup.
    const clearFocusPending = useRef(false);
    // Set when the removal settles: a readback superseded by `schedule/changed`
    // can publish its rows after the dialog closed, removing the trigger then.
    const clearSettled = useRef(false);
    const confirming = records.find(record => record.id === confirmId);
    // The bulk action's target and count come from the complete catalog, not from
    // the filtered rows: it removes every ended task, whatever the view shows.
    const endedCount = useMemo(() => records.filter(record => record.status === 'inactive').length, [records]);
    const rows = useMemo(() => {
        const query = search.trim().toLowerCase();
        return records.filter(record => ((statusFilter === 'all' || record.status === statusFilter)
            && (record.prompt.toLowerCase().includes(query)
                || record.sessionId.toLowerCase().includes(query)
                || taskName(record).toLowerCase().includes(query)))).toSorted((left, right) => Date.parse(left.scheduledAt) - Date.parse(right.scheduledAt));
    }, [records, search, statusFilter]);
    // An inactive-only filter is its own empty state: no task is inactive, rather than no task matched.
    const emptyTitle = statusFilter === 'inactive' && search.trim() === ''
        ? 'list.emptyInactive'
        : records.length === 0 ? 'list.empty' : 'list.noMatches';
    // The list's frequency line omits a rule zone equal to the host's and otherwise
    // names it, the same way the detail's Repeat row does.
    const systemZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const frequencyZone = { system: systemZone, label: zone => zoneLabel(zone, t) };
    const frequency = (record) => formatScheduleFrequency(record, t, frequencyZone);
    useEffect(() => {
        // The detail focuses its own panel; with nothing selected, focus returns
        // to the row that opened it, or to the page heading once that row is gone.
        // An open confirmation owns the focus until it closes, even when a refresh
        // drops the selected task behind it.
        if (selectedId !== null || clearConfirm)
            return;
        if (rowRef.current !== null) {
            const target = rowRef.current.isConnected ? rowRef.current : headingRef.current;
            target?.focus();
            rowRef.current = null;
        }
    }, [selectedId, clearConfirm]);
    useEffect(() => {
        if (status !== 'ready')
            return;
        if (selectedId !== null && selected === undefined)
            setSelectedId(null);
        if (confirmId !== null && confirming === undefined)
            setConfirmId(null);
    }, [status, selectedId, selected, confirmId, confirming]);
    const closeDetails = () => {
        setSelectedId(null);
    };
    // The Modal restores focus to the element it captured when it opened; the
    // trigger can be removed or rebuilt while the confirmation is open, so the
    // page points focus at the current control, or the heading when it is gone.
    // A settled removal whose readback lands later unmounts the trigger after the
    // dialog closed, which drops focus to the document body; that handoff is
    // caught by the same effect watching the ended count.
    useEffect(() => {
        if (clearConfirm)
            return;
        if (clearFocusPending.current) {
            clearFocusPending.current = false;
            const trigger = clearTriggerRef.current;
            if (trigger?.isConnected === true) {
                trigger.focus();
                return;
            }
            clearSettled.current = false;
            headingRef.current?.focus();
            return;
        }
        if (!clearSettled.current || endedCount > 0 || document.activeElement !== document.body)
            return;
        clearSettled.current = false;
        headingRef.current?.focus();
    }, [clearConfirm, endedCount]);
    const confirmClearEnded = () => {
        // The confirmation button stays focusable while the removal runs, so a
        // second press has to be ignored here instead of by `disabled`.
        if (clearing)
            return;
        setClearing(true);
        void onClearEnded().finally(() => {
            clearSettled.current = true;
            setClearing(false);
            setClearConfirm(false);
        });
    };
    return (_jsxs("section", { className: clsx(css.page, selected !== undefined && css.hasDetails), "aria-label": t('title'), "data-testid": "task-manager-page", onKeyDown: (event) => {
            // An Escape that closed a dropdown is already consumed by the Menu's
            // document capture listener. A confirmation's own Escape reaches the
            // Modal's document bubble listener, which runs after this React handler,
            // so the `clearConfirm` guard is what keeps this page from also closing
            // the detail while the dialog closes.
            if (event.key !== 'Escape' || event.defaultPrevented || confirmId !== null || clearConfirm
                || selectedId === null)
                return;
            event.preventDefault();
            event.stopPropagation();
            closeDetails();
        }, children: [_jsx("div", { className: css.listPane, children: _jsx("div", { className: css.pageScroll, children: _jsxs("div", { className: css.pageContent, children: [_jsxs("div", { className: css.pageHeading, children: [_jsx("h1", { ref: headingRef, tabIndex: -1, children: t('title') }), _jsx("div", { className: css.creationActions, children: _jsx(Button, { variant: "primary", size: "sm", className: css.headerAction, icon: _jsx(IconPlusOutlineRegular, { size: 13 }), onClick: onNewTask, children: t('new.action') }) })] }), _jsx("div", { className: css.filters, children: _jsx("div", { className: css.filterTabs, role: "group", "aria-label": t('statusFilter.label'), children: ['active', 'inactive', 'all'].map(value => (_jsx("button", { type: "button", className: clsx(css.filterTab, statusFilter === value && css.filterTabActive), "aria-pressed": statusFilter === value, onClick: () => { setStatusFilter(value); }, children: t(value === 'all' ? 'statusFilter.all' : `status.${value}`) }, value))) }) }), _jsxs("div", { className: css.searchTools, children: [_jsxs("div", { className: css.searchField, children: [_jsx(Input, { type: "search", icon: _jsx(IconSearchOutlineRegular, {}), "aria-label": t('search.label'), placeholder: t('search.placeholder'), value: search, onChange: (event) => { setSearch(event.target.value); } }), search !== '' && _jsx(Button, { size: "sm", className: css.searchClear, "aria-label": t('search.clear'), onClick: () => { setSearch(''); }, children: _jsx(IconCloseOutlineRegular, {}) })] }), statusFilter !== 'active' && endedCount > 0 && _jsx(Menu, { open: clearMenu, onClose: () => { setClearMenu(false); }, align: "end", portal: true, items: [{
                                                id: 'clear-inactive', label: t(clearing ? 'clear.pending' : 'clear.action'),
                                                icon: _jsx(IconTrashOutlineRegular, {}), danger: true,
                                                disabled: clearing || status !== 'ready',
                                            }], onSelect: () => {
                                            setClearMenu(false);
                                            clearFocusPending.current = true;
                                            setClearConfirm(true);
                                        }, anchor: _jsx(Tooltip, { label: t('list.more'), side: "bottom", portal: true, disabled: clearMenu, children: _jsx(Button, { ref: clearTriggerRef, variant: "outline", className: css.catalogMore, "aria-label": t('list.more'), "aria-haspopup": "menu", "aria-expanded": clearMenu, "aria-busy": clearing, onClick: () => { setClearMenu(value => !value); }, children: _jsx(IconEllipsisOutlineRegular, {}) }) }) })] }), _jsxs("div", { className: css.list, children: [selected === undefined && _jsx(CatalogFeedback, { status: status, populated: rows.length > 0, onRetry: onRetry, t: t }), status === 'ready' && rows.length === 0 && _jsxs("div", { className: css.empty, role: "status", children: [_jsx(ScheduleEmptyIllustration, { className: css.emptyIllustration }), _jsx("h2", { children: t(emptyTitle) }), _jsx(Button, { variant: "outline", className: css.emptyAction, onClick: onNewTask, children: t('empty.action') })] }), _jsx("ul", { className: css.listRows, "aria-label": t('list.label'), "aria-busy": status === 'loading', children: rows.map((record) => {
                                            // One pair per row: the same absolute stamp and distance reach
                                            // both halves, from one formatting call.
                                            const nextRun = nextRunParts(record.scheduledAt, t('time.locale'), now, t);
                                            return (_jsx("li", { children: _jsxs(Button, { className: clsx(css.row, selectedId === record.id && css.selectedRow, record.status === 'inactive' && css.endedRow), "aria-label": taskName(record), "aria-describedby": `${detailId}-metadata-${record.id}`, "aria-expanded": selectedId === record.id, "aria-controls": selectedId === record.id ? detailId : undefined, onClick: (event) => {
                                                        rowRef.current = event.currentTarget;
                                                        setSelectedId(record.id);
                                                        setTab('rule');
                                                    }, children: [_jsx(IconClockOutlineRegular, { className: css.rowGlyph }), _jsxs("span", { className: css.rowContent, children: [_jsx("span", { className: css.rowTitle, children: taskName(record) }), _jsxs("span", { className: css.rowSummary, id: `${detailId}-metadata-${record.id}`, children: [record.status === 'inactive'
                                                                            && _jsx("span", { className: css.metadata, children: t('status.inactive') }), _jsx("span", { className: css.metadata, children: frequency(record) }), record.status === 'active' && _jsxs("span", { className: css.metadata, children: [t('list.nextPrefix'), _jsx("time", { dateTime: record.scheduledAt, children: nextRun.absolute }), ' ', _jsx("span", { className: css.nextRunRelative, children: nextRun.relative })] })] })] })] }) }, record.id));
                                        }) })] })] }) }) }), selected !== undefined && _jsx(TaskDetail, { ...detail.props, task: selected, authoritative: catalogRecord !== undefined, onDeleted: closeDetails, onClose: closeDetails }), _jsx(Modal, { className: css.confirmDialog, contentClassName: css.confirmContent, open: clearConfirm, title: t('clear.title', { count: endedCount }), description: t('clear.description'), closeLabel: t('clear.close'), 
                // Dismissal waits for the settled removal: the confirmation is the only
                // place that states the operation is still running.
                onClose: () => { if (!clearing)
                    setClearConfirm(false); }, footer: _jsxs("div", { className: css.confirmActions, children: [_jsx(Button, { variant: "outline", disabled: clearing, onClick: () => { setClearConfirm(false); }, children: t('delete.cancel') }), _jsx(Button, { variant: "outline", className: css.deleteButton, "aria-disabled": clearing, onClick: confirmClearEnded, children: t(clearing ? 'clear.pending' : 'clear.confirm') })] }) })] }));
}
//# sourceMappingURL=TaskManagerPage.js.map