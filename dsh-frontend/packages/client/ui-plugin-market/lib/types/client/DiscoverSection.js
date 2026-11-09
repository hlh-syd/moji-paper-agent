import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
/** Discover: search one plugin market index, read an entry's page, and install a version. */
import { useEffect, useMemo, useState } from 'react';
import { Button, IconChevronDownOutlineRegular, IconPlusOutlineRegular, IconRefreshOutlineRegular, IconSearchOutlineRegular, IconSlidersTwoOutlineRegular, IconTrashOutlineRegular, IconWarningOutlineRegular, Input, Menu, Modal, Pill, PluginArtworkDefault, StateDot, Tag, Tooltip, } from '@deepseek-ai/dsh-client-ui-primitives';
import { displayDeclarations, indexLabel, presentedVersion, sourceFacts, withText, } from "./presentation.js";
import { PluginDetailSection } from "./PluginDetailSection.js";
import css from './DiscoverSection.module.css';
/** Typing pause before the query text is sent. */
const TEXT_DELAY_MS = 250;
/** Artwork size inside the 48px icon frame, as on the Installed tab. */
const ICON = 36;
const FAILURE_KEYS = {
    fetch: 'readFailed',
    invalid: 'readInvalid',
    changed: 'readChanged',
};
const FACT_KEYS = {
    package: 'factPackage',
    registry: 'factRegistry',
    repository: 'factRepository',
    commit: 'factCommit',
    directory: 'factDirectory',
};
function MarketIcon({ src, size }) {
    const [failed, setFailed] = useState();
    return src === undefined || src === failed
        ? _jsx(PluginArtworkDefault, { size: size })
        : (_jsx("img", { className: css.image, src: src, width: size, height: size, alt: "", loading: "lazy", referrerPolicy: "no-referrer", onError: () => { setFailed(src); } }));
}
function sourceFailureText(failure, t) {
    switch (failure.kind) {
        case 'invalid': return t('sourceInvalid');
        case 'duplicate': return t('sourceDuplicate');
        case 'unavailable': return t('sourceUnavailable');
        case 'other': return t('sourceFailed');
    }
}
function entryTitle(entry, face) {
    return entry.title === undefined ? entry.name : face.resolveText(entry.title);
}
/** Whether installation accepts a version: compatible, or covered by an exemption. */
function installable(compatibility) {
    return compatibility.compatible || compatibility.exempted;
}
function formatDate(timestamp, language, style) {
    return new Intl.DateTimeFormat(language, { dateStyle: style }).format(new Date(timestamp * 1000));
}
/** The one action an entry offers in its current state; Install chooses its default version. */
function EntryAction({ entry, title, t, onInstall, onOpenInstalled }) {
    const { status } = entry.installation;
    if (status !== 'not-installed') {
        return (_jsx(Button, { variant: "ghost", size: "sm", "aria-label": t('openInstalled', { name: title }), onClick: onOpenInstalled, children: t(status === 'update-available' ? 'statusUpdate' : status === 'provided' ? 'statusProvided' : 'statusInstalled') }));
    }
    const version = presentedVersion(entry);
    if (!version.compatibility.compatible && !version.compatibility.exempted) {
        return (_jsx(Tooltip, { label: t('noCompatibleVersion', { runtime: version.compatibility.runtimeVersion }), side: "bottom", portal: true, delayMs: 300, children: _jsx(Button, { variant: "outline", size: "sm", className: css.blocked, "aria-disabled": "true", "aria-label": t('installTitle', { name: title }), children: t('install') }) }));
    }
    return _jsx(Button, { variant: "outline", size: "sm", "aria-label": t('installTitle', { name: title }), onClick: () => { onInstall(version); }, children: t('install') });
}
function EntryCard({ entry, t, face, language, labels, onOpen, onInstall, onOpenInstalled }) {
    const title = entryTitle(entry, face);
    const version = presentedVersion(entry);
    const date = formatDate(version.publishTimestamp, language, 'medium');
    return (_jsxs("li", { className: css.card, "data-market-entry": entry.name, children: [_jsx("span", { className: css.icon, "aria-hidden": "true", children: _jsx(MarketIcon, { src: entry.icon, size: ICON }) }), _jsxs("div", { className: css.main, children: [_jsxs("div", { className: css.titleRow, children: [_jsx("button", { type: "button", className: css.cardOpen, "aria-label": t('openEntry', { name: title }), onClick: onOpen, children: title }), labels.filter(label => entry.metadata.labels.includes(label.id)).map(label => _jsx(Tag, { children: label.title }, label.id))] }), entry.description === undefined ? null : _jsx("span", { className: css.description, children: face.resolveText(entry.description) }), _jsx("span", { className: css.meta, children: entry.author === undefined
                            ? t('entryMeta', { version: version.version, date })
                            : t('entryMetaAuthor', { version: version.version, author: entry.author, date }) })] }), _jsx("div", { className: css.cardEnd, children: _jsx(EntryAction, { entry: entry, title: title, t: t, onInstall: onInstall, onOpenInstalled: onOpenInstalled }) })] }));
}
/** One row of the version list: its state tags and an Install button when installation accepts it. */
function VersionRow({ entry, version, title, t, language, onInstall }) {
    const current = entry.installation.installedVersion === version.version;
    // DSH ships a provided bundle; the market cannot replace it.
    const offered = !current && entry.installation.status !== 'provided' && installable(version.compatibility);
    const { compatibility } = version;
    return (_jsxs("li", { className: css.versionRow, "data-market-version": version.version, children: [_jsxs("div", { className: css.main, children: [_jsxs("div", { className: css.titleRow, children: [_jsx("span", { className: css.strong, children: version.version }), version.version === entry.defaultVersion ? _jsx(Tag, { tone: "info", children: t('versionDefault') }) : null, version.version.split('+')[0]?.includes('-') === true ? _jsx(Tag, { tone: "neutral", children: t('versionPrerelease') }) : null, current ? _jsx(Tag, { tone: "success", children: t('versionInstalled') }) : null, compatibility.compatible ? null : _jsx(Tag, { tone: "warning", children: t(compatibility.exempted ? 'versionExempted' : 'versionIncompatible') })] }), _jsx("span", { className: css.meta, children: compatibility.compatible
                            ? formatDate(version.publishTimestamp, language, 'medium')
                            : compatibility.exempted
                                ? t('exempted')
                                : t('incompatible', { runtime: compatibility.runtimeVersion, range: Object.values(compatibility.requirements).join(' ') }) })] }), !offered ? null : (_jsx(Button, { variant: "ghost", size: "sm", "aria-label": t('installVersionTitle', { name: title, version: version.version }), onClick: () => { onInstall(version); }, children: t('install') }))] }));
}
function EntryDetail({ entry, listing, t, face, hooks, language, onBack, onInstall, onOpenInstalled }) {
    const title = entryTitle(entry, face);
    const version = presentedVersion(entry);
    const date = formatDate(version.publishTimestamp, language, 'long');
    const categoryId = entry.metadata.category;
    const category = categoryId === undefined ? undefined : listing.metadata.categories[categoryId];
    const labels = displayDeclarations(listing.metadata.labels, face.resolveText, language)
        .filter(label => entry.metadata.labels.includes(label.id));
    const { compatibility } = version;
    return (_jsxs("div", { className: css.detail, "data-market-detail": entry.name, children: [_jsxs("button", { type: "button", className: css.crumb, onClick: onBack, children: [_jsx(IconChevronDownOutlineRegular, { className: css.crumbIcon, "aria-hidden": "true" }), _jsx("span", { children: t('backToList') })] }), _jsxs("div", { className: css.detailHead, children: [_jsx("span", { className: css.icon, "aria-hidden": "true", children: _jsx(MarketIcon, { src: entry.icon, size: ICON }) }), _jsxs("div", { className: css.main, children: [_jsxs("div", { className: css.titleRow, children: [_jsx("h3", { className: css.detailTitle, children: title }), _jsx(Tag, { tone: "neutral", children: version.version })] }), title === entry.name ? null : _jsx("span", { className: css.meta, children: entry.name })] }), _jsx(EntryAction, { entry: entry, title: title, t: t, onInstall: onInstall, onOpenInstalled: onOpenInstalled })] }), entry.description === undefined ? null : _jsx("p", { className: css.detailDescription, children: face.resolveText(entry.description) }), compatibility.compatible ? null : (_jsxs("p", { className: css.notice, role: "note", children: [_jsx(IconWarningOutlineRegular, { size: 14, "aria-hidden": "true" }), _jsx("span", { children: compatibility.exempted
                            ? t('exempted')
                            : t('incompatible', { runtime: compatibility.runtimeVersion, range: Object.values(compatibility.requirements).join(' ') }) })] })), _jsx("div", { className: css.detailDocument, children: _jsx(PluginDetailSection, { request: { url: listing.url, name: entry.name, version: version.version, npm: version.source.type === 'npm' }, t: t, loadDetail: face.loadDetail, retryDetail: face.retryDetail, detailImage: face.detailImage, useDetails: hooks.useDetails, useLocale: hooks.useLocale }) }), _jsxs("section", { className: css.detailSection, children: [_jsx("h4", { className: css.sectionTitle, children: t('versionsTitle') }), _jsx("ul", { className: css.versions, "aria-label": t('versionsLabel', { name: title }), children: entry.versions.map(item => (_jsx(VersionRow, { entry: entry, version: item, title: title, t: t, language: language, onInstall: onInstall }, item.version))) })] }), _jsxs("section", { className: css.detailSection, children: [_jsx("h4", { className: css.sectionTitle, children: t('factSource') }), _jsxs("dl", { className: css.facts, children: [entry.author === undefined ? null : _jsxs("div", { children: [_jsx("dt", { children: t('factAuthor') }), _jsx("dd", { children: entry.author })] }), entry.homepage === undefined ? null : (_jsxs("div", { children: [_jsx("dt", { children: t('factHomepage') }), _jsx("dd", { children: _jsx("a", { className: css.link, href: entry.homepage, target: "_blank", rel: "noopener noreferrer", children: entry.homepage }) })] })), entry.repository === undefined ? null : (_jsxs("div", { children: [_jsx("dt", { children: t('factSourceCode') }), _jsxs("dd", { children: [_jsx("a", { className: css.link, href: entry.repository.url, target: "_blank", rel: "noopener noreferrer", children: entry.repository.url }), entry.repository.directory === undefined ? null : _jsxs(_Fragment, { children: [" ", _jsx("code", { children: entry.repository.directory })] })] })] })), sourceFacts(entry.name, version).map(fact => (_jsxs("div", { children: [_jsx("dt", { children: t(FACT_KEYS[fact.kind]) }), _jsx("dd", { children: _jsx("code", { children: fact.value }) })] }, fact.kind))), _jsxs("div", { children: [_jsx("dt", { children: t('factPublished') }), _jsx("dd", { children: date })] }), category === undefined ? null : _jsxs("div", { children: [_jsx("dt", { children: t('factCategory') }), _jsx("dd", { children: face.resolveText(category.title) })] }), labels.length === 0 ? null : (_jsxs("div", { children: [_jsx("dt", { children: t('factLabels') }), _jsx("dd", { className: css.tags, children: labels.map(label => _jsx(Tag, { children: label.title }, label.id)) })] })), _jsxs("div", { children: [_jsx("dt", { children: t('factMarket') }), _jsx("dd", { children: face.resolveText(listing.title) })] })] })] })] }));
}
function SourcesDialog({ state, t, face }) {
    return (_jsx(Modal, { open: state.editing, onClose: face.toggleSources, title: t('sourcesTitle'), closeLabel: t('close'), description: t('sourcesDescription'), className: css.dialog, children: _jsxs("div", { className: css.dialogBody, children: [state.indexes.length === 0 ? null : (_jsx("ul", { className: css.sourceList, "aria-label": t('sourcesLabel'), children: state.indexes.map(index => (_jsxs("li", { className: css.sourceRow, children: [_jsxs("div", { className: css.main, children: [_jsx("span", { className: css.strong, children: indexLabel(index, face.resolveText) }), _jsx("span", { className: css.meta, children: index.url }), index.error === undefined ? null : _jsx("span", { className: css.sourceError, children: index.error })] }), _jsx(Tooltip, { label: t('removeSource'), side: "bottom", portal: true, delayMs: 300, children: _jsx(Button, { variant: "ghost", size: "sm", icon: _jsx(IconTrashOutlineRegular, { size: 13 }), "aria-label": t('removeSourceNamed', { url: index.url }), disabled: state.saving, onClick: () => { face.removeSource(index.url); } }) })] }, index.url))) })), _jsxs("form", { className: css.sourceForm, onSubmit: (event) => { event.preventDefault(); face.addSource(); }, children: [_jsx(Input, { className: css.grow, value: state.draft, placeholder: t('sourcePlaceholder'), "aria-label": t('sourceUrl'), "aria-invalid": state.sourceFailure !== null, "data-modal-autofocus": true, onChange: (event) => { face.editDraft(event.target.value); } }), _jsx(Button, { type: "submit", variant: "primary", size: "sm", icon: _jsx(IconPlusOutlineRegular, { size: 13 }), disabled: state.saving || state.draft.trim() === '', children: t('addSource') })] }), state.sourceFailure === null ? null : _jsx("p", { className: css.error, role: "alert", children: sourceFailureText(state.sourceFailure, t) }), _jsxs("p", { className: css.notice, role: "note", children: [_jsx(IconWarningOutlineRegular, { size: 14, "aria-hidden": "true" }), _jsx("span", { children: t('sourcesTrust') })] })] }) }));
}
function ConfirmDialog({ choice, t, face, onCancel, onConfirm }) {
    if (choice === null)
        return null;
    const { entry, version } = choice;
    const title = entryTitle(entry, face);
    return (_jsx(Modal, { open: true, onClose: onCancel, title: t('confirmTitle', { name: title }), closeLabel: t('close'), className: css.dialog, footer: (_jsxs(_Fragment, { children: [_jsx(Button, { variant: "outline", onClick: onCancel, children: t('cancel') }), _jsx(Button, { variant: "primary", autoFocus: true, onClick: () => { onConfirm(choice); }, children: t('install') })] })), children: _jsxs("div", { className: css.dialogBody, children: [_jsxs("div", { className: css.confirmHead, children: [_jsx("span", { className: css.icon, "aria-hidden": "true", children: _jsx(MarketIcon, { src: entry.icon, size: ICON }) }), _jsxs("div", { className: css.main, children: [_jsx("span", { className: css.strong, children: title }), _jsx("span", { className: css.meta, children: version.version })] })] }), _jsxs("dl", { className: css.facts, children: [entry.author === undefined ? null : _jsxs("div", { children: [_jsx("dt", { children: t('factAuthor') }), _jsx("dd", { children: entry.author })] }), sourceFacts(entry.name, version).map(fact => (_jsxs("div", { children: [_jsx("dt", { children: t(FACT_KEYS[fact.kind]) }), _jsx("dd", { children: _jsx("code", { children: fact.value }) })] }, fact.kind)))] }), version.compatibility.compatible ? null : (_jsxs("p", { className: css.notice, role: "note", children: [_jsx(IconWarningOutlineRegular, { size: 14, "aria-hidden": "true" }), _jsx("span", { children: t('exempted') })] })), _jsxs("p", { className: css.notice, role: "note", children: [_jsx(IconWarningOutlineRegular, { size: 14, "aria-hidden": "true" }), _jsx("span", { children: t('installTrust') })] })] }) }));
}
function ListSkeleton({ label }) {
    return (_jsx("ul", { className: css.cards, role: "status", "aria-label": label, "aria-busy": "true", children: [0, 1, 2, 3].map(index => (_jsxs("li", { className: css.card, "aria-hidden": "true", children: [_jsx("span", { className: `${css.icon} ${css.skeletonFill}` }), _jsxs("div", { className: css.main, children: [_jsx("span", { className: `${css.skeletonBar} ${css.skeletonTitle} ${css.skeletonFill}` }), _jsx("span", { className: `${css.skeletonBar} ${css.skeletonDescription} ${css.skeletonFill}` })] })] }, index))) }));
}
/** The sort menu id of a sort. */
function sortMenuId(sort) {
    return sort.kind === 'key' ? `key:${sort.id}` : sort.kind;
}
/** Whether a query selects every plugin of the index. */
function unfiltered(query) {
    return query.text.trim() === '' && query.category === null && query.labels.length === 0;
}
function Browser({ state, listing, t, face, hooks, language, onInstall, openPackage }) {
    const { query, value } = listing;
    const [text, setText] = useState(query.text);
    const [menu, setMenu] = useState(null);
    const [opened, setOpened] = useState(null);
    useEffect(() => {
        if (text === query.text)
            return undefined;
        const timer = setTimeout(() => { face.search(withText(query, text)); }, TEXT_DELAY_MS);
        return () => { clearTimeout(timer); };
    }, [text, query, face]);
    const declarations = useMemo(() => ({
        categories: value === null ? [] : displayDeclarations(value.metadata.categories, face.resolveText, language),
        labels: value === null ? [] : displayDeclarations(value.metadata.labels, face.resolveText, language),
        sortKeys: value === null ? [] : displayDeclarations(value.metadata.sortKeys, face.resolveText, language),
    }), [value, face, language]);
    // A reload keeps the opened plugin current while its page is loaded.
    const openEntry = opened === null ? undefined : value?.entries.find(entry => entry.name === opened.name) ?? opened;
    if (openEntry !== undefined && value !== null) {
        return (_jsx(EntryDetail, { entry: openEntry, listing: value, t: t, face: face, hooks: hooks, language: language, onBack: () => { setOpened(null); }, onInstall: (version) => { onInstall({ entry: openEntry, version }); }, onOpenInstalled: () => { openPackage(openEntry.name); } }));
    }
    const selected = state.indexes.find(index => index.url === listing.url) ?? { url: listing.url };
    const market = indexLabel(selected, face.resolveText);
    const { sort } = query;
    const sortOptions = [
        ...query.text.trim() === '' ? [] : [{ id: 'relevance', label: t('sortRelevance') }],
        { id: 'index', label: t('sortDefault') },
        { id: 'published', label: t('sortPublished') },
        ...declarations.sortKeys.map(item => ({ id: `key:${item.id}`, label: item.title })),
    ];
    // The Controller drops a sort key the index no longer declares, and relevance follows the text.
    const sortName = sortOptions.find(option => option.id === sortMenuId(sort)).label;
    const directed = sort.kind === 'published' || sort.kind === 'key';
    const chooseSort = (id) => {
        setMenu(null);
        if (id === 'direction' && directed) {
            face.search({ ...query, sort: { ...sort, direction: sort.direction === 'asc' ? 'desc' : 'asc' } });
            return;
        }
        if (id === 'index' || id === 'relevance') {
            face.search({ ...query, sort: { kind: id } });
            return;
        }
        if (id === 'published') {
            face.search({ ...query, sort: { kind: 'published', direction: 'desc' } });
            return;
        }
        const key = declarations.sortKeys.find(item => `key:${item.id}` === id);
        /* v8 ignore next -- every other menu id names a declared sort key */
        if (key !== undefined)
            face.search({ ...query, sort: { kind: 'key', id: key.id, direction: key.declaration.order } });
    };
    const toggleMenu = (name) => { setMenu(menu === name ? null : name); };
    const busy = listing.status === 'loading' && value !== null;
    return (_jsxs(_Fragment, { children: [_jsxs("div", { className: css.toolbar, children: [_jsx(Menu, { open: menu === 'market', onClose: () => { setMenu(null); }, portal: true, autoFocus: true, selectedId: listing.url, items: state.indexes.map(index => ({ id: index.url, label: indexLabel(index, face.resolveText) })), footer: [{ id: 'manage', label: t('manageSources'), icon: _jsx(IconSlidersTwoOutlineRegular, { size: 14 }) }], onSelect: (id) => {
                            setMenu(null);
                            if (id === 'manage')
                                face.toggleSources();
                            else
                                face.select(id);
                        }, anchor: (_jsxs(Button, { variant: "ghost", size: "sm", className: css.marketButton, "aria-haspopup": "menu", "aria-expanded": menu === 'market', "aria-label": t('marketMenu', { market }), onClick: () => { toggleMenu('market'); }, children: [_jsx("span", { className: css.marketName, children: market }), _jsx(IconChevronDownOutlineRegular, { size: 12, "aria-hidden": "true" })] })) }), _jsx(Input, { className: css.search, type: "search", icon: _jsx(IconSearchOutlineRegular, { size: 14 }), value: text, placeholder: t('search'), "aria-label": t('search'), onChange: (event) => { setText(event.target.value); } }), declarations.labels.length === 0 ? null : (_jsx(Menu, { open: menu === 'filter', onClose: () => { setMenu(null); }, portal: true, autoFocus: true, align: "end", selectedIds: query.labels, items: declarations.labels.map(item => ({ id: item.id, label: item.title })), onSelect: (id) => {
                            const labels = query.labels.includes(id) ? query.labels.filter(label => label !== id) : [...query.labels, id];
                            face.search({ ...query, labels });
                        }, anchor: (_jsx(Button, { variant: "ghost", size: "sm", "aria-haspopup": "menu", "aria-expanded": menu === 'filter', icon: _jsx(IconSlidersTwoOutlineRegular, { size: 13 }), onClick: () => { toggleMenu('filter'); }, children: query.labels.length === 0 ? t('filter') : t('filterCount', { count: String(query.labels.length) }) })) })), _jsx(Menu, { open: menu === 'sort', onClose: () => { setMenu(null); }, portal: true, autoFocus: true, align: "end", selectedId: sortMenuId(sort), items: sortOptions, ...directed
                            ? { footer: [{ id: 'direction', label: t(sort.direction === 'asc' ? 'switchToDescending' : 'switchToAscending') }] }
                            : {}, onSelect: chooseSort, anchor: (_jsxs(Button, { variant: "ghost", size: "sm", "aria-haspopup": "menu", "aria-expanded": menu === 'sort', onClick: () => { toggleMenu('sort'); }, children: [t('sortBy', { sort: sortName }), _jsx(IconChevronDownOutlineRegular, { size: 12, "aria-hidden": "true" })] })) }), _jsx(Tooltip, { label: t('refresh'), side: "bottom", portal: true, delayMs: 500, focusDelayMs: 500, children: _jsx(Button, { variant: "ghost", size: "sm", "aria-label": t('refresh'), disabled: listing.status === 'loading', onClick: face.refresh, icon: busy ? _jsx(StateDot, { state: "ongoing" }) : _jsx(IconRefreshOutlineRegular, { size: 14 }) }) })] }), declarations.categories.length === 0 ? null : (_jsxs("div", { className: css.categories, role: "group", "aria-label": t('categoriesLabel'), children: [_jsx(Pill, { active: query.category === null, "aria-pressed": query.category === null, onClick: () => { face.search({ ...query, category: null }); }, children: t('allCategories') }), declarations.categories.map(item => (_jsx(Pill, { active: query.category === item.id, "aria-pressed": query.category === item.id, onClick: () => { face.search({ ...query, category: item.id }); }, children: item.title }, item.id)))] })), listing.failure === null ? null : (_jsxs("div", { className: css.failure, children: [_jsxs("p", { className: css.error, role: "alert", children: [_jsx(StateDot, { state: "error" }), t(FAILURE_KEYS[listing.failure])] }), _jsx(Button, { variant: "outline", size: "sm", onClick: face.retry, children: t('retry') })] })), value === null
                ? listing.status === 'loading' ? _jsx(ListSkeleton, { label: t('loading') }) : null
                : value.total === 0
                    // Results are published together with the query they answer.
                    ? _jsx("p", { className: css.muted, children: t(unfiltered(listing.answered) ? 'emptyIndex' : 'noMatches') })
                    : (_jsxs(_Fragment, { children: [_jsx("ul", { className: css.cards, "aria-busy": busy, children: value.entries.map(entry => (_jsx(EntryCard, { entry: entry, t: t, face: face, language: language, labels: declarations.labels, onOpen: () => { setOpened(entry); }, onInstall: (version) => { onInstall({ entry, version }); }, onOpenInstalled: () => { openPackage(entry.name); } }, entry.name))) }), value.nextOffset === null
                                ? null
                                : (_jsx(Button, { variant: "outline", size: "sm", className: css.more, disabled: listing.status !== 'ready', onClick: face.more, children: t('showMore') }))] }))] }));
}
/** Render the Discover tab of the Plugins page. */
export function DiscoverSection(props) {
    const { t, install, openPackage } = props;
    const face = props;
    const state = props.useDiscover(snapshot => snapshot);
    const language = props.useLocale(snapshot => snapshot.active);
    const [confirming, setConfirming] = useState(null);
    useEffect(() => { props.ensure(); }, [props.ensure]);
    let body;
    if (state.status === 'idle' || state.status === 'loading') {
        body = _jsx(ListSkeleton, { label: t('loading') });
    }
    else if (state.status === 'error') {
        body = (_jsxs("div", { className: css.failure, children: [_jsxs("p", { className: css.error, role: "alert", children: [_jsx(StateDot, { state: "error" }), t('sourcesFailed')] }), _jsx(Button, { variant: "outline", size: "sm", onClick: props.reload, children: t('retry') })] }));
    }
    else if (state.listing === null) {
        body = (_jsxs("div", { className: css.empty, children: [_jsx(PluginArtworkDefault, { size: 48 }), _jsx("p", { className: css.emptyTitle, children: t('emptyTitle') }), _jsx("p", { className: css.muted, children: t('emptyDescription') }), _jsx(Button, { variant: "primary", size: "sm", icon: _jsx(IconPlusOutlineRegular, { size: 13 }), onClick: props.toggleSources, children: t('addFirstSource') })] }));
    }
    else {
        body = (_jsx(Browser, { state: state, listing: state.listing, t: t, face: face, hooks: { useDetails: props.useDetails, useLocale: props.useLocale }, language: language, onInstall: (choice) => { setConfirming(choice); }, openPackage: openPackage }, state.listing.url));
    }
    return (_jsxs("div", { className: css.section, "data-plugin-tab": "discover", children: [body, _jsx(SourcesDialog, { state: state, t: t, face: face }), _jsx(ConfirmDialog, { choice: confirming, t: t, face: face, onCancel: () => { setConfirming(null); }, onConfirm: ({ entry, version }) => {
                    setConfirming(null);
                    install({ ...version.install, title: entryTitle(entry, face), version: version.version });
                } })] }));
}
//# sourceMappingURL=DiscoverSection.js.map