import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * Sidebar shell: column geometry and global panel navigation.
 * Collapse is a slide plus crossfade:
 * content freezes at its expanded width (inline style) and fades out in place
 * while the sliding column (AppFrame grid tracks) clips it — nothing reflows
 * mid-slide. At settle the wide-only content unmounts and the upper
 * controls enter the 56px rail from the same horizontal offset (one icon each,
 * same top-down order) on one fade that ends with the slide. The bottom-pinned
 * settings control only fades. The workspace/session browsing region between
 * global panel rows and the foot is the `sidebar.workspaces` registrant's,
 * and the foot holds `sidebar.settings` plus `sidebar.footer.action`; the shell
 * hands them the wide flag (plus an expand request callback for the browser).
 *
 * The column also owns whether the scroll regions nested in it draw a
 * scrollbar at all: the shell tracks the pointer and rebinds ui-theme's
 * scrollbar indirection away while it is elsewhere, so a list the user is not
 * pointing at carries no bar.
 */
import { useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import { FishLogo, IconAppsAddOutlineRegular, IconNewChatOutlineMedium, IconNewChatOutlineRegular, IconPanelLeftOutlineRegular, isDarwinDesktop, Menu, ShortcutKeys, Toast, Tooltip, writeClipboard, } from '@deepseek-ai/dsh-client-ui-primitives';
import { FIXED_PANEL_ID } from "./panel-pins.js";
import css from './SidebarRoot.module.css';
/** Wide-content unmount delay; matches the 150ms wide-content fade-out. */
const COLLAPSE_SETTLE_MS = 150;
/**
 * How long the column's scrollbars stay drawn after the pointer leaves it.
 * The bar is a pointer affordance here, and hiding it on the leave event
 * itself makes it blink out while the pointer is only crossing the column's
 * edge — on the way to the conversation, or around a portalled menu.
 */
const SCROLLBAR_LINGER_MS = 2000;
/** Format complete-build metadata for the local brand badge. */
function localBuildVersion() {
    const version = process.env.DSH_CLIENT_VERSION;
    if (version === undefined)
        return undefined;
    const commit = process.env.DSH_CLIENT_COMMIT_HASH;
    return version
        + (commit === undefined ? '' : `-${commit}`)
        + (process.env.DSH_CLIENT_GIT_DIRTY === 'true' ? '-dirty' : '');
}
/** Analytics category of an official panel; other panels are never reported. */
function officialEntry(id) {
    if (id === 'plugins')
        return 'plugin';
    return id === 'schedules' ? 'cron' : undefined;
}
/** Each panel row subscribes only to its own selection state. */
function PanelRow({ id, label, wide, usePanelInfo, selectPanel, trackEntryClick, renderSlot }) {
    const active = usePanelInfo(info => info.activePanelId === id);
    return (_jsx(Tooltip, { label: label, delayMs: 500, disabled: wide, children: _jsxs("button", { type: "button", className: clsx(css.panelRow, active && css.panelActive), "aria-label": label, "aria-current": active ? 'page' : undefined, onClick: () => {
                const entry = officialEntry(id);
                if (entry !== undefined)
                    trackEntryClick({ menu_name: entry, position: 'sidebar' });
                selectPanel(id);
            }, children: [_jsx("span", { className: css.panelGlyph, "aria-hidden": "true", children: renderSlot('sidebar.panellist', { size: wide ? 16 : 18, active }, { only: id }) }), wide && (_jsx("span", { className: clsx(css.panelTitle, css.wide), children: label }))] }) }));
}
/** The panel list's More launcher: a panel-row button whose menu rows come from `sidebar.panellist.actions`. */
function PanelMoreMenu({ wide, label, entries, onOpen, onPinnedChange, trackEntryClick, renderSlot }) {
    const [open, setOpen] = useState(false);
    const onDismiss = () => { setOpen(false); };
    return (_jsx(Menu, { open: open, onClose: onDismiss, portal: true, autoFocus: true, className: css.moreAnchor, listClassName: css.moreMenu, anchor: (_jsx(Tooltip, { label: label, delayMs: 500, disabled: wide, children: _jsxs("button", { type: "button", className: css.panelRow, "aria-label": label, "aria-haspopup": "menu", "aria-expanded": open, onClick: () => {
                    trackEntryClick({ menu_name: 'more', position: 'sidebar' });
                    setOpen(value => !value);
                }, children: [_jsx("span", { className: css.panelGlyph, "aria-hidden": "true", children: _jsx(IconAppsAddOutlineRegular, { size: wide ? 16 : 18 }) }), wide && _jsx("span", { className: clsx(css.panelTitle, css.wide), children: label })] }) })), children: renderSlot('sidebar.panellist.actions', { entries, onOpen, onPinnedChange, onDismiss }) }));
}
/**
 * Render the sidebar column shell.
 * @param props - composed slot props (runtime share + injected callbacks, contract/slots.ts).
 * @returns the sidebar element tree.
 */
export function SidebarRoot({ collapsed, width, startSession, toggleSidebar, selectPanel, setPinned, trackEntryClick, useRemoteAccess, usePanels, useEntryPins, useEntryActions, useShortcuts, usePanelInfo, t, renderSlot, }) {
    const panels = usePanels(snapshot => snapshot);
    const unpinned = useEntryPins(ids => ids);
    const activePanelId = usePanelInfo(info => info.activePanelId);
    const hasEntryActions = useEntryActions(registered => registered);
    const shortcut = useShortcuts(rows => rows.find(row => row.id === 'sidebar.left.toggle'));
    const newShortcut = useShortcuts(rows => rows.find(row => row.id === 'session.new'));
    // A page whose access snapshot excludes starting a Session offers no New Session affordance.
    const canStartSession = useRemoteAccess('session/create');
    const toggleLabel = collapsed ? t('toggle.open') : t('toggle.collapse');
    // Wide content stays mounted while the collapse animates (fading via
    // .collapsed .wide), unmounts at settle, and remounts right away on expand.
    const [settled, setSettled] = useState(collapsed);
    useEffect(() => {
        if (!collapsed) {
            setSettled(false);
            return;
        }
        const timer = window.setTimeout(() => { setSettled(true); }, COLLAPSE_SETTLE_MS);
        return () => { window.clearTimeout(timer); };
    }, [collapsed]);
    const windowsTitlebar = document.documentElement.hasAttribute('data-windows-titlebar');
    const wide = windowsTitlebar ? !collapsed : !collapsed || !settled;
    // The Windows titlebar hosts the New Session controls while the sidebar is collapsed.
    const openNewSession = () => {
        trackEntryClick({ menu_name: 'new_session', position: windowsTitlebar && collapsed ? 'titlebar' : 'sidebar' });
        startSession();
    };
    // The Windows caption menus occupy the strip to the right of these controls
    // (that is what --dsh-windows-menu-start reserves), so a right-side bubble
    // lands under their text. Below the caption is the only clear side.
    const captionTooltipSide = windowsTitlebar ? 'bottom' : 'right';
    // Freeze the content at its expanded width while it fades out (collapsed
    // && wide): the sliding column then clips it instead of reflowing it. The
    // rail layout (.collapsed styles) only applies once the fade settles.
    const lastWideWidth = useRef(width);
    if (!collapsed)
        lastWideWidth.current = width;
    // Rail-in only crossfades a live collapse: a refresh straight into the
    // collapsed state renders the rail statically (no delay-hidden icons).
    const everWide = useRef(!collapsed);
    if (!collapsed)
        everWide.current = true;
    // Scrollbars in the column follow the pointer (.quietBars rebinds them
    // away): drawn while it is inside, and for SCROLLBAR_LINGER_MS after it
    // leaves. A pointer that returns within that window cancels the pending
    // hide rather than restarting from a hidden bar.
    const column = useRef(null);
    const [pointerInside, setPointerInside] = useState(false);
    const lingerTimer = useRef(undefined);
    const armLinger = () => {
        if (lingerTimer.current !== undefined)
            return;
        lingerTimer.current = window.setTimeout(() => {
            lingerTimer.current = undefined;
            setPointerInside(false);
        }, SCROLLBAR_LINGER_MS);
    };
    const cancelLinger = () => {
        window.clearTimeout(lingerTimer.current);
        lingerTimer.current = undefined;
    };
    // Leaving is decided by the column's BOX, not by DOM containment, and only
    // while the bars are drawn. ui-settings renders its full-viewport panel as a
    // fixed-position DESCENDANT of this column, so a pointer moved onto that
    // panel — or onto the conversation once it closes — fires no `pointerleave`
    // here, and the bars would stay drawn over a column nobody is pointing at.
    // The element's own leave stays as the one signal geometry cannot give: a
    // pointer that leaves the window emits no further moves.
    useEffect(() => {
        if (!pointerInside)
            return;
        const onMove = (event) => {
            const rect = column.current?.getBoundingClientRect();
            /* v8 ignore next -- the listener only exists while the column is mounted and revealed. */
            if (rect === undefined)
                return;
            const inside = event.clientX >= rect.left && event.clientX < rect.right
                && event.clientY >= rect.top && event.clientY < rect.bottom;
            if (inside)
                cancelLinger();
            else
                armLinger();
        };
        document.addEventListener('pointermove', onMove);
        return () => {
            document.removeEventListener('pointermove', onMove);
            cancelLinger();
        };
    }, [pointerInside]);
    const buildVersion = localBuildVersion();
    // The local-build fallbacks copy the badge text; the brand button below
    // skips New Session for clicks inside them. The click still bubbles, so
    // document-level outside-click listeners see it.
    // Each successful copy bumps the key so a repeated copy replays the toast.
    const [copiedToast, setCopiedToast] = useState(null);
    const copyVersion = (version) => () => {
        void writeClipboard(version).then((ok) => {
            if (ok)
                setCopiedToast(seq => (seq ?? 0) + 1);
        });
    };
    const darwinDesktop = isDarwinDesktop();
    // Rail resting state is the whale mark; hovering swaps in the panel icon
    // (the expand affordance, figma sidebar-hover flow). Expanded it is a plain
    // panel icon.
    const toggle = (_jsx(Tooltip, { label: toggleLabel, shortcutKeys: shortcut?.keys, delayMs: 500, side: captionTooltipSide, children: _jsxs("button", { type: "button", className: clsx(css.iconButton, css.toggle), "aria-label": toggleLabel, "aria-keyshortcuts": shortcut?.aria, onClick: () => { toggleSidebar(); }, children: [!wide && !windowsTitlebar && (_jsx("span", { className: css.railMark, "aria-hidden": "true", children: renderSlot('sidebar.brand.mark', { size: 24 }, { fallback: _jsx(FishLogo, { size: 24 }) }) })), _jsx(IconPanelLeftOutlineRegular, { className: css.panelIcon, size: wide || windowsTitlebar ? 16 : 18 }), !wide && renderSlot('sidebar.toggle.badge', {})] }) }));
    return (_jsxs("div", { ref: column, className: clsx(css.root, !wide && css.collapsed, !wide && everWide.current && css.railIn, collapsed && wide && css.fading, !pointerInside && css.quietBars), style: wide ? { width: collapsed ? lastWideWidth.current : width } : undefined, onPointerEnter: () => {
            cancelLinger();
            setPointerInside(true);
        }, onPointerLeave: () => { armLinger(); }, children: [darwinDesktop && _jsx("div", { className: css.topStrip, "data-window-drag": true, children: toggle }), _jsxs("div", { className: css.logoRow, "data-window-drag": true, children: [wide && (() => {
                        const identity = (_jsxs("span", { className: css.brandIdentity, "aria-hidden": "true", children: [_jsx("span", { className: css.brandMark, children: renderSlot('sidebar.brand.mark', { size: 24 }, {
                                        fallback: buildVersion === undefined
                                            ? _jsx(FishLogo, { size: 24 })
                                            : _jsx("span", { className: css.copyVersion, "data-copy-version": true, onClick: copyVersion(buildVersion), children: _jsx(FishLogo, { size: 24 }) }),
                                    }) }), _jsx("span", { className: css.brandName, children: renderSlot('sidebar.brand.name', {}, {
                                        fallback: buildVersion === undefined
                                            ? _jsx("span", { className: css.fallbackBrandName, children: t('brand.localBuild') })
                                            : (_jsxs("span", { className: clsx(css.localBuildBrand, css.copyVersion), "data-copy-version": true, onClick: copyVersion(buildVersion), children: [_jsx("span", { className: css.localBuildTitle, children: t('brand.localBuild') }), _jsx("span", { className: css.buildVersion, children: buildVersion })] })),
                                    }) })] }));
                        return darwinDesktop || !canStartSession
                            ? _jsx("span", { className: clsx(css.brand, css.wide), children: identity })
                            : (_jsx("button", { type: "button", className: clsx(css.brand, css.wide), "aria-label": t('session.new.label'), "aria-keyshortcuts": newShortcut?.aria, onClick: (event) => {
                                    if (event.target.closest('[data-copy-version]') !== null)
                                        return;
                                    openNewSession();
                                }, children: identity }));
                    })(), !darwinDesktop && toggle] }), canStartSession && (_jsx(Tooltip, { label: t('session.new.label'), shortcutKeys: newShortcut?.keys, delayMs: 500, side: captionTooltipSide, disabled: wide, children: _jsxs("button", { type: "button", className: css.newSession, "aria-label": t('session.new.label'), "aria-keyshortcuts": newShortcut?.aria, onClick: openNewSession, children: [_jsx("span", { className: css.newSessionLabelMask, children: _jsxs("span", { className: css.newSessionContent, children: [wide
                                        ? _jsx(IconNewChatOutlineMedium, { size: 14 })
                                        : _jsx(IconNewChatOutlineRegular, { size: windowsTitlebar ? 16 : 18 }), wide && _jsx("span", { className: clsx(css.newSessionLabel, css.wide), children: t('session.new') })] }) }), wide && newShortcut !== undefined && newShortcut.keys.length > 0 && _jsx("span", { className: css.newSessionShortcut, "aria-hidden": "true", children: _jsx(ShortcutKeys, { keys: newShortcut.keys }) })] }) })), panels.length > 0 && (_jsxs("nav", { className: css.panelList, "aria-label": t('panels.label'), children: [panels.filter(({ id }) => id === FIXED_PANEL_ID || !unpinned.includes(id)).map(({ id, label }) => (_jsx(PanelRow, { id: id, label: label, wide: wide, usePanelInfo: usePanelInfo, selectPanel: selectPanel, trackEntryClick: trackEntryClick, renderSlot: renderSlot }, id))), hasEntryActions && (_jsx(PanelMoreMenu, { wide: wide, label: t('panels.more'), entries: panels.filter(({ id }) => id !== FIXED_PANEL_ID)
                            .map(({ id, label }) => ({ id, label, pinned: !unpinned.includes(id), active: id === activePanelId })), onOpen: (id) => {
                            const entry = officialEntry(id);
                            if (entry !== undefined)
                                trackEntryClick({ menu_name: entry, position: 'sidebar_more' });
                            const panel = panels.find(candidate => candidate.id === id);
                            if (panel !== undefined)
                                selectPanel(panel.id);
                        }, onPinnedChange: setPinned, trackEntryClick: trackEntryClick, renderSlot: renderSlot }))] })), _jsx("div", { className: css.regionArea, children: renderSlot('sidebar.workspaces', {
                    wide,
                    expandSidebar: () => { if (collapsed)
                        toggleSidebar(); },
                }) }), copiedToast !== null && (_jsx(Toast, { tone: "success", text: t('version.copied'), onDone: () => { setCopiedToast(null); } }, copiedToast)), _jsxs("div", { className: css.footArea, children: [_jsx("div", { className: css.footerActions, children: renderSlot('sidebar.footer.action', { wide }) }), _jsx("div", { className: css.settingsArea, children: renderSlot('sidebar.settings', { wide }) })] })] }));
}
//# sourceMappingURL=SidebarRoot.js.map