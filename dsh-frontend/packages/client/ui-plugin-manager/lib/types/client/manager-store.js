import { sanitizeInstallInput } from "./sanitize-install-input.js";
import { randomUUID } from '@deepseek-ai/dsh-util-crypto';
import { normalizeRegistry, NPMMIRROR_REGISTRY, OFFICIAL_NPM_REGISTRY, REGISTRY_URL } from '@deepseek-ai/dsh-plugin-manager/registry';
import { createSnapshotStore } from '@deepseek-ai/dsh-client-store';
import { shortName } from "./presentation.js";
import { BundleDetailController } from "./detail-store.js";
/**
 * Read whether a bundle has a pending management action.
 * @param state Current manager snapshot.
 * @param packageName Bundle whose controls and observers must wait.
 * @returns Whether the bundle's management operation is still pending.
 */
export function bundleBusy(state, packageName) {
    return state.busy.includes(packageName) || state.update?.packageName === packageName
        || (state.install.subject?.name === packageName && (isInstallPending(state.install.phase) || state.install.phase === 'checking'));
}
/** The choice shown until the Host has said which registry it asks first: the one pnpm's own configuration names. */
const OFFICIAL_REGISTRY = { kind: 'offered', registry: null };
/** Hold the manual-refresh spinner at least this long so a fast read does not flash it. */
const REFRESH_SPINNER_MIN_MS = 400;
/**
 * The registry a choice asks, as the Host's install plan compares registries: pnpm's own configuration stands for
 * the URL it names, once the Host has read it.
 * @param registry - the registry, null for the one pnpm's own configuration names.
 * @param resolved - the URL pnpm's own configuration names, null while the Host could not read it.
 * @returns the comparison key; a registry that does not parse compares as written.
 */
export function registryKey(registry, resolved) {
    const url = registry ?? resolved;
    if (url === null)
        return '';
    try {
        return normalizeRegistry(url);
    }
    catch {
        // The Host validated its own registries; a remembered one that no longer parses compares as written.
        return url;
    }
}
/**
 * The registries the dialog offers: the Host's first, its fallbacks, and pnpm's own, each once. pnpm's own
 * configuration stands for the registry it names, so it never repeats a registry the Host already offers.
 * @param registries - what the Host configured, or null while unread.
 * @returns the registries in the order the dialog lists them.
 */
export function offeredRegistries(registries) {
    const offered = [];
    const keys = [];
    for (const registry of [...registries === null ? [] : [registries.registry, ...registries.fallbackRegistries], null]) {
        const key = registryKey(registry, registries?.resolved ?? null);
        if (keys.includes(key))
            continue;
        offered.push(registry);
        keys.push(key);
    }
    return offered;
}
/**
 * Whether an installation is still owned by the Host.
 * @param phase - the dialog's current installation phase.
 * @returns true while a Host result or a check for an active request is outstanding.
 */
export function isInstallPending(phase) {
    return phase === 'starting' || phase === 'running' || phase === 'cancelling' || phase === 'applying' || phase === 'unconfirmed';
}
/**
 * Offer the configured mainland mirror after a confirmed GitHub connection failure.
 * @param install - the installation and the Host's failure attribution.
 * @returns the offered entry that asks npmmirror, null when that entry is pnpm's own configuration, or undefined when
 * this recovery does not apply; test for undefined, because null is a valid entry.
 */
export function githubRecoveryRegistry(install) {
    // The mirror recovery reopens the spec form, which a confirmed spec does not have.
    if (install.confirmed !== undefined || install.phase !== 'failed' || install.failure?.failedAt !== 'spec-host'
        || (install.failure.kind !== 'network' && install.failure.kind !== 'timeout'))
        return undefined;
    const host = install.subject?.host?.toLowerCase().split(':')[0];
    if (host !== 'github.com' && !host?.endsWith('.github.com'))
        return undefined;
    const resolved = install.registries?.resolved ?? null;
    return offeredRegistries(install.registries).find(registry => registryKey(registry, resolved) === NPMMIRROR_REGISTRY);
}
/**
 * Whether the install already asks npmmirror first, so switching to the offered mirror would not change the registry.
 * @param install - the installation and its registry choice.
 * @returns true when the chosen registry, offered or typed, compares as npmmirror.
 */
export function asksMirror(install) {
    const choice = install.registry;
    return registryKey(choice.kind === 'custom' ? choice.url.trim() : choice.registry, install.registries?.resolved ?? null) === NPMMIRROR_REGISTRY;
}
/**
 * The version an update offers first: the newest one that declares a bundle, accepts the running DSH version, and is
 * neither deprecated nor a prerelease.
 * @param list - the Host's version list.
 * @returns the version, or undefined when none qualifies or the list was refused.
 */
export function updateCandidate(list) {
    if (list.status !== 'listed')
        return undefined;
    return list.versions.find(version => installable(version) && version.deprecated === undefined && !version.prerelease);
}
/**
 * Whether the Host can install a listed version in place of the installed one.
 * @param version - one listed version.
 * @returns true when it declares a bundle and accepts the running DSH version.
 */
export function installable(version) {
    return version.bundle && version.incompatible === undefined;
}
/** A refused answer or a change the Host could not apply, carrying what it said and, for a refusal, its code. */
class RemoteAnswerError extends Error {
    reason;
    code;
    incompatible;
    constructor(reason, code, incompatible) {
        super(reason);
        this.reason = reason;
        this.code = code;
        this.incompatible = incompatible;
        this.name = 'RemoteAnswerError';
    }
}
/** The dialog's reading of a failed change: the Host's code and diagnostic, and the run's classified failure. */
function failureOf(error, kind, pendingBuilds, failedAt) {
    return {
        reason: error?.diagnostic ?? '',
        ...error === undefined ? {} : { code: error.code },
        ...error?.incompatible === undefined ? {} : { incompatible: error.incompatible },
        ...kind === undefined ? {} : { kind },
        ...failedAt === undefined ? {} : { failedAt },
        ...pendingBuilds === undefined || pendingBuilds.length === 0 ? {} : { pendingBuilds },
    };
}
/** The notice a thrown failure becomes: a refusal keeps its code, anything else its words. */
function failedNotice(error, subject, seq) {
    const code = error instanceof RemoteAnswerError ? error.code : undefined;
    const incompatible = error instanceof RemoteAnswerError ? error.incompatible : undefined;
    return {
        kind: 'failed', reason: reasonOf(error), ...code === undefined ? {} : { code },
        ...incompatible === undefined ? {} : { incompatible }, ...subject, seq,
    };
}
/** The runs with every one still open settled at `exitCode`. */
function settledRuns(runs, exitCode) {
    return runs.map(run => run.exitCode === undefined ? { ...run, exitCode } : run);
}
/**
 * The key one row occupies in the busy list.
 * @param entryId - the row's Loader entry id.
 * @returns the busy key.
 */
export function rowKey(entryId) {
    return `row:${entryId}`;
}
/**
 * One bundle as the page shows it: its rows joined with the Host's entries.
 * @param bundle - the Host's bundle.
 * @param plugins - the Host's plugin entries.
 * @returns the package view.
 */
export function packageView(bundle, plugins) {
    const rows = bundle.rows.map((row) => {
        const live = row.entryId === undefined ? undefined : plugins.find(plugin => plugin.entryId === row.entryId);
        return {
            rowId: row.rowId,
            moduleName: row.moduleName,
            enabled: live?.enabled ?? false,
            phase: live?.fiberPhase ?? null,
            ...row.meta === undefined ? {} : { meta: row.meta },
            ...row.entryId === undefined ? {} : { entryId: row.entryId },
            ...live?.readOnlyReason === undefined ? {} : { readOnlyReason: live.readOnlyReason },
        };
    });
    return {
        name: bundle.name,
        official: bundle.official,
        availability: bundle.availability,
        ...bundle.installTarget === undefined ? {} : { installTarget: bundle.installTarget },
        installed: bundle.installed,
        ...bundle.listedByDefault === undefined ? {} : { listedByDefault: bundle.listedByDefault },
        optional: bundle.optional,
        removable: bundle.removable,
        updatable: bundle.updatable,
        enabled: bundle.enabled,
        rows,
        ...bundle.version === undefined ? {} : { version: bundle.version },
        ...bundle.source === undefined ? {} : { source: bundle.source },
        ...bundle.description === undefined ? {} : { description: bundle.description },
        ...bundle.meta === undefined ? {} : { meta: bundle.meta },
        ...bundle.readOnlyReason === undefined ? {} : { readOnlyReason: bundle.readOnlyReason },
        ...bundle.error === undefined ? {} : { error: bundle.error },
    };
}
/**
 * The order the list shows packages in: by the short name a person reads, so a
 * card stays put when its bundle is switched, whatever order the Host answers in.
 * @param packages - the Host's bundles as views.
 * @returns the views sorted by short name.
 */
export function sortPackages(packages) {
    return [...packages].sort((a, b) => shortName(a.name).localeCompare(shortName(b.name)));
}
const IDLE_INSTALL = {
    open: false, spec: '', registries: null, registry: OFFICIAL_REGISTRY, registryOpen: false, registryError: false, attempts: null,
    phase: 'idle', inputError: null, subject: null, runs: [], detailsOpen: false,
    installed: null, installedVersion: null, restartRequired: false, failure: null, approvedBuilds: [], enabling: false,
};
/** The dialog back at its spec: the run and its outcome forgotten, the spec and the registries kept. */
function specAgain(install) {
    const { open, spec, registries, registry, mirrorRecovery, confirmed } = install;
    return {
        ...IDLE_INSTALL, open, spec, registries, registry,
        ...install.subject?.selection === undefined ? {} : { subject: install.subject },
        ...mirrorRecovery === undefined ? {} : { mirrorRecovery }, ...confirmed === undefined ? {} : { confirmed },
    };
}
/**
 * The choice as the dialog can show it once the Host has answered: nothing remembered starts from the registry the
 * Host asks first; a remembered registry the Host now asks under another entry takes that entry, and one it no
 * longer offers is kept as a typed one.
 */
function reconciled(remembered, registries) {
    if (remembered === null)
        return { kind: 'offered', registry: registries.registry };
    if (remembered.kind === 'custom' || remembered.registry === null)
        return remembered;
    const key = registryKey(remembered.registry, registries.resolved);
    const offered = offeredRegistries(registries)
        .find(registry => registryKey(registry, registries.resolved) === key);
    return offered === undefined ? { kind: 'custom', url: remembered.registry } : { kind: 'offered', registry: offered };
}
/** Only the shipped public mirror can replace an unconfigured official npm default. */
function eligibleMirror(registries) {
    if (registries.registry !== null || registries.resolved === null)
        return undefined;
    let resolved;
    try {
        resolved = normalizeRegistry(registries.resolved);
    }
    catch (_error) {
        // An invalid pnpm registry cannot receive a public mirror recommendation.
        return undefined;
    }
    if (resolved !== OFFICIAL_NPM_REGISTRY)
        return undefined;
    return registries.fallbackRegistries.find(registry => registry === NPMMIRROR_REGISTRY);
}
/** Reads and mutates the profile's plugins through the `pluginManager` Remote. */
export class PluginManagerController {
    ctx;
    store;
    inFlight;
    rerun = false;
    generation = 0;
    disposed = false;
    /** A successful managed-profile read remains usable even when it returned no bundles. */
    hasCachedInventory = false;
    pendingConfirm;
    /** Cancels the check the dialog has in flight. */
    inspectAbort;
    request;
    noticeSeq = 0;
    analyticsAttempt;
    registryRead;
    /** A Discover entry chose the dialog's registry; that choice applies to this dialog only and is never remembered. */
    transientRegistry = false;
    /** Bundle detail documents the page shows. */
    details;
    /** The registry last used from this browser, kept across dialogs and page loads; null until one was used. */
    registryMemory = createSnapshotStore(null, {
        persist: { name: 'dsh.plugin-manager.install-registry' },
    });
    /**
     * @param ctx - the tab plugin's context, whose `remote.pluginManager` and `remote.pluginInventory` namespaces answer.
     */
    constructor(ctx) {
        this.ctx = ctx;
        this.details = new BundleDetailController(ctx, () => document.baseURI);
        this.store = createSnapshotStore({
            status: 'idle', refreshStatus: 'idle', packages: [], busy: [], notice: null,
            install: IDLE_INSTALL, confirm: null, highlight: null, versions: {}, update: null,
        });
    }
    /**
     * Read the tab's state.
     * @returns the current sync snapshot (stable reference until the next change).
     */
    getSnapshot() {
        return this.store.getSnapshot();
    }
    /**
     * Observe existing bundle and row operations independently of the page's lifetime.
     * @param packageName Bundle whose management activity is observed.
     * @returns A read-only source; unsubscribe releases the caller's observation.
     */
    observeChanges(packageName) {
        return {
            getSnapshot: () => {
                const state = this.getSnapshot();
                return bundleBusy(state, packageName) || (state.packages.find(pkg => pkg.name === packageName)?.rows.some(row => row.entryId !== undefined && state.busy.includes(rowKey(row.entryId))) ?? false);
            },
            subscribe: listener => this.store.subscribe(listener),
        };
    }
    /** Stop publishing and drop every late settlement. */
    dispose() {
        this.disposed = true;
        this.details.dispose();
        this.registryRead = undefined;
        this.request = undefined;
        this.generation += 1;
    }
    /**
     * Build the face the tab's slot registration injects.
     * @param configLedger - the projection of the plugins carrying configuration, bound beside the tab's own state.
     * @param pluginTabs - the tabs other plugins contribute through `plugins.tab`.
     * @param resolveText - render-time package text resolution supplied by the locale service.
     * @returns the tab's snapshot sources and its actions.
     */
    inject(configLedger, pluginTabs, resolveText) {
        const details = this.details;
        return {
            resolveText,
            hooks: {
                pluginManager: this.store, configLedger, pluginTabs, configurations: this.ctx.configForms.describe(),
                bundleDetails: details.store, locale: this.ctx.locale,
            },
            loadDetail: (name, version, language, fromNpm) => { details.load(name, version, language, fromNpm); },
            retryDetail: (name, version, language, fromNpm) => { details.retry(name, version, language, fromNpm); },
            detailImage: (name, document, destination, fromNpm) => details.image(name, document, destination, fromNpm),
            configForm: id => this.ctx.configForms.get(id),
            ensure: () => { if (this.getSnapshot().status === 'idle')
                void this.load(); },
            refresh: () => { void this.refresh(); },
            openInstall: () => {
                this.ctx.get('productAnalytics')?.track('plugin_add_button_click', {});
                this.openDialog();
            },
            installFrom: ({ spec, registry, title, version }) => {
                // A hidden installation reopens as it is; only one installation runs at a time.
                if (!this.openDialog())
                    return;
                this.transientRegistry = registry !== undefined;
                this.patchInstall({ spec, confirmed: { title, version }, ...registry === undefined ? {} : { registry: { kind: 'custom', url: registry } } });
                void this.runInstall();
            },
            closeInstall: () => {
                if (isInstallPending(this.getSnapshot().install.phase)) {
                    this.patchInstall({ open: false });
                    void this.cancelInstall();
                    return;
                }
                if (this.getSnapshot().install.phase === 'checking' || this.getSnapshot().install.subject?.bundled)
                    this.finishAnalytics('cancelled');
                this.abortInspect();
                this.registryRead = undefined;
                this.patch({ install: IDLE_INSTALL });
            },
            editInstallSpec: (text) => {
                const install = this.getSnapshot().install;
                // Typing while the Host checks or installs is not possible; a new spec after an outcome starts over.
                if (install.phase === 'checking' || isInstallPending(install.phase))
                    return;
                this.patchInstall(install.phase === 'idle' ? { spec: text, inputError: null, subject: null }
                    : { ...specAgain(install), spec: text, subject: null });
            },
            runInstall: () => { void this.runInstall(); },
            toggleRegistryOptions: () => { this.patchInstall({ registryOpen: !this.getSnapshot().install.registryOpen }); },
            chooseRegistry: (choice) => {
                if (this.getSnapshot().install.phase !== 'idle')
                    return;
                this.transientRegistry = false;
                this.patchInstall({ registry: choice, registryError: false });
            },
            changeRegistry: () => {
                const install = this.getSnapshot().install;
                if (install.phase === 'failed')
                    this.patch({ install: { ...specAgain(install), registryOpen: true } });
            },
            useGithubMirror: () => {
                const install = this.getSnapshot().install;
                const mirror = githubRecoveryRegistry(install);
                if (mirror === undefined)
                    return;
                const recovered = { ...specAgain(install), spec: '', mirrorRecovery: true };
                // A typed address or pnpm's own entry that already asks the mirror stays chosen instead of becoming the offered one.
                if (asksMirror(install)) {
                    this.patch({ install: recovered });
                    return;
                }
                const registry = { kind: 'offered', registry: mirror };
                this.registryMemory.set(registry);
                this.patch({ install: { ...recovered, registry } });
            },
            approveBuildsAndRetry: () => { void this.approveBuildsAndRetry(); },
            cancelInstall: () => { void this.cancelInstall(); },
            reconcileInstall: () => { void this.reconcileInstall(); },
            toggleInstallDetails: () => { this.patchInstall({ detailsOpen: !this.getSnapshot().install.detailsOpen }); },
            enableInstalled: () => { void this.enableInstalled(); },
            clearHighlight: () => { if (this.getSnapshot().highlight !== null)
                this.patch({ highlight: null }); },
            setEnabled: (packageName, enabled) => {
                const pkg = this.getSnapshot().packages.find(pkg => pkg.name === packageName);
                if (enabled && pkg?.availability === 'missing' && pkg.installTarget !== undefined) {
                    this.openCatalogInstall(pkg, pkg.installTarget, true);
                    return;
                }
                void this.run(packageName, { packageName, action: enabled ? 'enable' : 'disable' }, async () => {
                    const result = await this.ctx.remote.pluginManager.setBundleEnabled(packageName, enabled);
                    this.applied(result, packageName);
                    if (result.ok && (result.value.application === 'applied' || result.value.application === 'restart-required'))
                        this.trackToggle(packageName, enabled);
                });
            },
            update: (packageName) => {
                const pkg = this.getSnapshot().packages.find(pkg => pkg.name === packageName);
                const target = pkg?.installTarget;
                if (pkg === undefined || target === undefined || pkg.availability === 'installation')
                    return;
                if (!pkg.installed) {
                    this.openCatalogInstall(pkg, target, pkg.enabled);
                    return;
                }
                // An installed package is removed before another version is installed. Without HMR a running bundle stays
                // installed, and the installation over it waits for the restart.
                void this.run(packageName, { packageName, action: 'uninstall' }, async () => {
                    const removed = await this.ctx.remote.pluginManager.removeBundle(packageName);
                    if (removed.ok && removed.value.error?.code === 'stop-profile') {
                        this.openCatalogInstall(pkg, target, pkg.enabled);
                        return;
                    }
                    // A failed removal throws; without HMR a removal waits for the restart, and the installation follows it.
                    this.applied(removed, packageName);
                    this.openCatalogInstall(pkg, target, pkg.enabled);
                });
            },
            uninstall: (packageName) => {
                this.pendingConfirm = () => this.run(packageName, { packageName, action: 'uninstall' }, async () => {
                    this.ctx.get('productAnalytics')?.track('confirm_uninstall_plugin', { plugin_name: packageName });
                    this.applied(await this.ctx.remote.pluginManager.removeBundle(packageName), packageName);
                });
                this.patch({ confirm: { action: 'uninstall', packageName } });
            },
            confirm: () => { void this.confirm(); },
            cancelConfirm: () => { this.pendingConfirm = undefined; this.patch({ confirm: null }); },
            setRowEnabled: (entryId, enabled) => {
                void this.run(rowKey(entryId), { packageName: entryId, action: enabled ? 'rowEnable' : 'rowDisable' }, async () => {
                    const result = await this.ctx.remote.pluginManager.setPluginEnabled(entryId, enabled);
                    this.applied(result, entryId);
                    if (result.ok && (result.value.application === 'applied' || result.value.application === 'restart-required'))
                        this.trackToggle(entryId, enabled, true);
                });
            },
            dismissNotice: () => { this.patch({ notice: null }); },
            checkVersions: (packageName) => { void this.checkVersions(packageName); },
            ensureVersions: (packageName) => {
                if (this.getSnapshot().versions[packageName] === undefined)
                    void this.checkVersions(packageName);
            },
            openUpdate: (packageName, version) => {
                const check = this.getSnapshot().versions[packageName];
                if (this.getSnapshot().update?.phase === 'running' || check?.phase !== 'done' || check.list.status !== 'listed')
                    return;
                this.patch({ update: {
                        packageName, from: check.list.installed, to: version, registry: check.list.registry, phase: 'confirm', steps: [], result: null, outputOpen: false,
                    } });
            },
            runUpdate: () => { void this.runUpdate(); },
            closeUpdate: () => { if (this.getSnapshot().update?.phase !== 'running')
                this.patch({ update: null }); },
            toggleUpdateOutput: () => {
                const update = this.getSnapshot().update;
                if (update !== null)
                    this.patch({ update: { ...update, outputOpen: !update.outputOpen } });
            },
        };
    }
    /**
     * Open the install dialog: a new one starts from the registry last used here and reads what the Host offers; a hidden
     * installation reopens as it is.
     * @returns whether a new dialog started.
     */
    openDialog() {
        const started = this.getSnapshot().install.requestId === undefined;
        if (started) {
            this.transientRegistry = false;
            this.patch({ install: { ...IDLE_INSTALL, open: true, registry: this.registryMemory.getSnapshot() ?? OFFICIAL_REGISTRY } });
            const read = { choice: this.getSnapshot().install.registry };
            this.registryRead = read;
            read.done = this.readRegistries(read).finally(() => { delete read.done; });
        }
        else {
            this.patchInstall({ open: true });
        }
        void this.reconcileInstall();
        return started;
    }
    /**
     * Follow the Host's steps of this tab's update.
     * @param progress - a request id and step received from the Host.
     */
    updateProgress(progress) {
        const update = this.getSnapshot().update;
        if (update?.phase !== 'running' || update.requestId !== progress.requestId || update.steps.includes(progress.step))
            return;
        this.patch({ update: { ...update, steps: [...update.steps, progress.step] } });
    }
    /** Read one package's newer versions; a transport failure reads as an unknown refusal. */
    async checkVersions(packageName) {
        const { versions, update } = this.getSnapshot();
        // A check during the package's update would answer for the version being replaced.
        if (this.disposed || versions[packageName]?.phase === 'checking' || (update?.phase === 'running' && update.packageName === packageName))
            return;
        this.patchVersions(packageName, { phase: 'checking' });
        // The generated Remote takes every business argument before its optional signal; `{}` asks the Host's own plan.
        const answer = await this.ctx.remote.pluginManager.listVersions(packageName, {});
        const list = answer.ok ? answer.value : { status: 'refused', problem: 'unknown', reason: answer.error.message, attempts: [] };
        this.patchVersions(packageName, { phase: 'done', list, checkedAt: Date.now() });
    }
    /** Check the updatable packages one after another, so one click starts at most one package's registry lookups. */
    async checkAllVersions() {
        for (const pkg of this.getSnapshot().packages.filter(item => item.updatable))
            await this.checkVersions(pkg.name);
    }
    /** Hand the confirmed update to the Host, keep its outcome, then read the package and its versions again. */
    async runUpdate() {
        const update = this.getSnapshot().update;
        if (this.disposed || update === null || update.phase !== 'confirm')
            return;
        const requestId = randomUUID();
        const running = { ...update, phase: 'running', steps: [], requestId, result: null };
        this.patch({ update: running });
        const answer = await this.ctx.remote.pluginManager.updateBundle(update.packageName, update.to, { registry: update.registry, requestId });
        // Nothing closes or replaces a running update before its answer; after disposal `patch` publishes nothing.
        const current = this.getSnapshot().update;
        this.patch({ update: { ...current, phase: 'done', ...answer.ok ? { result: answer.value } : { lost: answer.error.message } } });
        await this.load();
        await this.checkVersions(update.packageName);
    }
    /**
     * Forget the checks that no longer describe a listed package: one that left the list or stopped being updatable, and
     * one answered for another installed version, after a reinstallation or a change from another surface.
     */
    dropStaleVersions() {
        const { packages, versions } = this.getSnapshot();
        const current = Object.fromEntries(Object.entries(versions).filter(([name, check]) => {
            const pkg = packages.find(item => item.name === name);
            return pkg?.updatable === true && (check.phase === 'checking' || check.list.status !== 'listed' || check.list.installed === pkg.version);
        }));
        if (Object.keys(current).length !== Object.keys(versions).length)
            this.patch({ versions: current });
    }
    patchVersions(packageName, check) {
        this.patch({ versions: { ...this.getSnapshot().versions, [packageName]: check } });
    }
    /**
     * Follow the Host's cancellation window for this dialog's installation.
     * @param progress - a request id and phase received from the Host.
     */
    installProgress(progress) {
        const install = this.getSnapshot().install;
        if (install.requestId !== progress.requestId || !isInstallPending(install.phase))
            return;
        const request = this.request;
        if (request === undefined)
            return;
        request.acknowledged = true;
        // Host progress cannot reopen a cancellation window that has already closed.
        if (install.phase === 'applying')
            return;
        if (progress.phase === 'installing' && request.cancellation !== undefined) {
            if (request.cancellation.waitingForStart)
                void this.sendCancellation(request, request.cancellation);
            return;
        }
        const attempt = progress.attempt;
        this.patchInstall({
            phase: progress.phase === 'installing' ? 'running' : progress.phase,
            ...attempt === undefined
                ? {}
                : { attempts: { registries: [...install.attempts?.registries ?? [], attempt.registry], total: attempt.total } },
        });
    }
    currentRegistryRead(read) {
        return !this.disposed && this.registryRead === read;
    }
    /** Read the registries the Host offers, for the dialog just opened; a refused read leaves pnpm's own and a typed one. */
    async readRegistries(read) {
        const answer = await this.ctx.remote.pluginManager.registries();
        const install = this.getSnapshot().install;
        if (!this.currentRegistryRead(read) || !install.open || !answer.ok)
            return;
        const remembered = this.registryMemory.getSnapshot();
        const untouched = install.registry === read.choice && (install.phase === 'idle' || install.phase === 'checking');
        if (untouched)
            read.choice = reconciled(remembered, answer.value);
        this.patchInstall({ registries: answer.value, ...untouched ? { registry: read.choice } : {} });
        const mirror = eligibleMirror(answer.value);
        if (!untouched || remembered !== null || mirror === undefined)
            return;
        const fastest = await this.ctx.remote.pluginRegistryProbe.fastest();
        const current = this.getSnapshot().install;
        if (!this.currentRegistryRead(read) || !current.open || (current.phase !== 'idle' && current.phase !== 'checking')
            || current.registry !== read.choice || !fastest.ok || fastest.value !== mirror)
            return;
        read.choice = { kind: 'offered', registry: mirror };
        this.patchInstall({ registry: read.choice });
    }
    /**
     * Fold a chunk belonging to this installation into its pnpm command.
     * A final chunk may arrive after the install answer and still updates an existing run.
     * @param chunk - the chunk the Host forwarded.
     */
    appendLog(chunk) {
        const install = this.getSnapshot().install;
        if (chunk.requestId !== install.requestId)
            return;
        // Output also acknowledges an install when its start notification was lost.
        if (install.requestId !== undefined && (install.phase === 'starting' || install.phase === 'cancelling' || install.phase === 'unconfirmed')) {
            this.installProgress({ requestId: install.requestId, phase: 'installing' });
        }
        const index = install.runs.findIndex(run => run.jobId === chunk.jobId);
        if (index === -1 && !isInstallPending(install.phase))
            return;
        const settled = chunk.exitCode === undefined ? {} : { exitCode: chunk.exitCode };
        const runs = index === -1
            ? [...install.runs, { jobId: chunk.jobId, command: chunk.argv.join(' '), cwd: chunk.cwd, output: chunk.text, ...settled }]
            : install.runs.map((run, at) => at === index ? { ...run, output: run.output + chunk.text, ...settled } : run);
        this.patchInstall({ runs });
    }
    /**
     * Read the bundles and the entries their rows run as. A call during an
     * in-flight read marks one rerun after it settles.
     * @returns settlement after this call's freshness is reflected.
     */
    load() {
        if (this.disposed)
            return Promise.resolve();
        void this.reconcileInstall();
        if (this.inFlight !== undefined) {
            this.rerun = true;
            return this.inFlight;
        }
        const run = Promise.resolve().then(() => this.read());
        this.inFlight = run;
        return run;
    }
    /**
     * Keep manual refresh feedback until its coalesced reads and the version checks of the updatable packages settle,
     * without clearing cached cards; a failed read checks nothing.
     */
    async refresh() {
        if (this.disposed || this.getSnapshot().refreshStatus === 'refreshing')
            return;
        const startedAt = Date.now();
        this.patch({
            refreshStatus: 'refreshing',
            ...this.getSnapshot().notice?.kind === 'refresh-failed' ? { notice: null } : {},
        });
        try {
            await this.load();
            if (this.getSnapshot().status !== 'error')
                await this.checkAllVersions();
        }
        catch (_error) {
            // A rejected transport request leaves the cached cards available for retry.
            this.patch({ status: 'error' });
        }
        finally {
            // Hold the spinner to its minimum so a fast read does not flash it, then settle.
            const remaining = REFRESH_SPINNER_MIN_MS - (Date.now() - startedAt);
            if (remaining > 0)
                await new Promise((resolve) => { setTimeout(resolve, remaining); });
            this.settleRefresh();
        }
    }
    /** Publish refresh feedback only before disposal. */
    settleRefresh() {
        if (this.disposed)
            return;
        const failed = this.getSnapshot().status === 'error';
        this.patch(failed && this.hasCachedInventory
            ? { status: 'ready', refreshStatus: 'idle', notice: { kind: 'refresh-failed', seq: ++this.noticeSeq } }
            : { refreshStatus: failed ? 'failed' : 'idle' });
    }
    async read() {
        try {
            do {
                this.rerun = false;
                const generation = ++this.generation;
                if (this.getSnapshot().status === 'idle')
                    this.patch({ status: 'loading' });
                // The manager Remote is mounted whether or not the Host manages a
                // profile; the inventory says whether it does.
                const inventory = await this.ctx.remote.pluginInventory.list();
                if (generation !== this.generation)
                    return;
                if (!inventory.ok) {
                    this.patch({ status: 'error' });
                    continue;
                }
                if (inventory.value.managementAvailable !== true) {
                    this.hasCachedInventory = false;
                    this.patch({ status: 'unavailable', packages: [] });
                    continue;
                }
                const [bundles, plugins] = await Promise.all([
                    this.ctx.remote.pluginManager.listBundles(),
                    this.ctx.remote.pluginManager.listPlugins(),
                ]);
                if (generation !== this.generation)
                    return;
                if (!bundles.ok || !plugins.ok) {
                    this.patch({ status: 'error' });
                    continue;
                }
                this.hasCachedInventory = true;
                this.patch({
                    status: 'ready',
                    refreshStatus: this.getSnapshot().refreshStatus === 'refreshing' ? 'refreshing' : 'idle',
                    packages: sortPackages(bundles.value.map(bundle => packageView(bundle, plugins.value))),
                });
                this.dropStaleVersions();
            } while (this.shouldRerun());
        }
        finally {
            this.inFlight = undefined;
        }
    }
    shouldRerun() {
        return this.rerun;
    }
    async confirm() {
        const pending = this.pendingConfirm;
        this.pendingConfirm = undefined;
        this.patch({ confirm: null });
        if (pending !== undefined)
            await pending();
    }
    /** Drop the check in flight; its answer is ignored. */
    abortInspect() {
        this.inspectAbort?.abort();
        this.inspectAbort = undefined;
    }
    /** Whether a settlement arrives too late to matter: the store is disposed, or the dialog moved on. */
    gone(signal) {
        return this.disposed || signal.aborted;
    }
    /** Reuse the install dialog with an exact catalog target and an explicit selection outcome. */
    openCatalogInstall(pkg, target, enabled) {
        if (this.disposed)
            return;
        const { phase } = this.getSnapshot().install;
        if (phase === 'checking' || isInstallPending(phase)) {
            this.patchInstall({ open: true });
            return;
        }
        this.abortInspect();
        const registry = this.registryMemory.getSnapshot() ?? OFFICIAL_REGISTRY;
        const local = target.spec.startsWith('link:');
        const subject = {
            status: 'accepted', kind: local ? 'path' : 'registry', name: pkg.name, version: target.version, bundle: true,
            spec: target.spec, registry: registry.kind === 'custom' ? registry.url : registry.registry,
            selection: enabled, ...local ? {} : { saveExact: true },
        };
        this.patch({ install: { ...IDLE_INSTALL, open: true, spec: subject.spec, registry, subject } });
        const read = { choice: registry };
        this.registryRead = read;
        read.done = this.readRegistries(read).finally(() => { delete read.done; });
        void this.runInstall();
    }
    /**
     * Offer a default-hidden shipped bundle for explicit enablement, or check
     * and install the typed spec. A refused spec returns to the field with its
     * reason; an accepted one becomes the subject shown while pnpm runs.
     */
    async runInstall() {
        const state = this.getSnapshot();
        const install = state.install;
        const spec = install.spec.trim();
        if (install.phase === 'checking' || isInstallPending(install.phase) || spec === '')
            return;
        if (this.ctx.get('productAnalytics')?.enabled) {
            this.analyticsAttempt = { input: sanitizeInstallInput(spec), started: Date.now() };
            this.ctx.get('productAnalytics')?.track('plugin_install_click', { input_value: sanitizeInstallInput(spec) });
        }
        const listed = state.packages.find(pkg => pkg.name === spec);
        const catalogSubject = install.subject?.selection === undefined || install.subject.spec !== spec ? undefined : install.subject;
        if (listed?.availability === 'installation' && listed.optional && listed.listedByDefault === false
            && !listed.enabled && listed.error === undefined && listed.readOnlyReason === undefined) {
            this.abortInspect();
            this.patch({ install: {
                    ...IDLE_INSTALL, open: true, spec, registries: install.registries, registry: install.registry,
                    phase: 'done', installed: listed.name, installedVersion: listed.version ?? null,
                    subject: { status: 'accepted', kind: 'registry', bundle: true, name: listed.name, spec, registry: null, bundled: true,
                        ...listed.version === undefined ? {} : { version: listed.version },
                        ...listed.description === undefined ? {} : { description: listed.description } },
                } });
            return;
        }
        if (listed !== undefined && listed.availability !== 'missing' && catalogSubject === undefined) {
            this.finishAnalytics('failed', 'already-installed');
            this.patchInstall({ phase: 'idle', inputError: { problem: listed.availability === 'installation' ? 'shipped' : 'already-installed', reason: spec } });
            return;
        }
        const choice = install.registry;
        const typed = choice.kind === 'custom' ? choice.url.trim() : undefined;
        if (typed !== undefined && !REGISTRY_URL.test(typed)) {
            this.finishAnalytics('failed', 'invalid-registry');
            this.patchInstall({ phase: 'idle', registryError: true, registryOpen: true });
            return;
        }
        this.abortInspect();
        const controller = new AbortController();
        this.inspectAbort = controller;
        this.patchInstall({
            phase: 'checking', inputError: null, subject: catalogSubject ?? null, runs: [], detailsOpen: false, attempts: null, registryOpen: false,
            installed: null, installedVersion: null, restartRequired: false, failure: null, approvedBuilds: [],
        });
        const read = this.registryRead;
        if (read !== undefined && choice === read.choice && read.done !== undefined) {
            await read.done;
            if (this.gone(controller.signal) || this.registryRead !== read)
                return;
        }
        const currentChoice = this.getSnapshot().install.registry;
        const selected = currentChoice.kind === 'custom' ? { ...currentChoice, url: currentChoice.url.trim() } : currentChoice;
        const registry = selected.kind === 'custom' ? selected.url : selected.registry;
        if (!this.transientRegistry)
            this.registryMemory.set(selected);
        if (catalogSubject !== undefined) {
            this.inspectAbort = undefined;
            await this.startInstall({ ...catalogSubject, registry });
            return;
        }
        const inspected = await this.ctx.remote.pluginManager.inspect(spec, { registry }, controller.signal);
        if (this.gone(controller.signal))
            return;
        this.inspectAbort = undefined;
        if (!inspected.ok) {
            this.finishAnalytics('failed', inspected.error.code);
            this.patchInstall({ phase: 'idle', inputError: { problem: 'unknown', reason: inspected.error.message } });
            return;
        }
        if (inspected.value.status === 'refused') {
            const { problem, reason, registries } = inspected.value;
            this.finishAnalytics('failed', problem);
            this.patchInstall({ phase: 'idle', inputError: { problem, reason, ...registries === undefined ? {} : { registries } } });
            return;
        }
        await this.startInstall({ spec, ...inspected.value });
    }
    /**
     * Hand the checked spec to the Host and settle the dialog from its answer.
     * `approvedBuilds` names the pending install scripts the person allowed;
     * the Host saves that permission for this profile before pnpm runs.
     */
    async startInstall(subject, approvedBuilds) {
        const { spec, registry } = subject;
        const requestId = randomUUID();
        const request = { requestId, acknowledged: false, replyLost: false, recovering: false };
        this.request = request;
        this.patchInstall({ phase: 'starting', requestId, subject, runs: [], attempts: null, failure: null, installed: null, installedVersion: null, approvedBuilds: [] });
        // The Host announces `plugin-manager/changed` while the run is still on
        // the wire, and every such event reads again; those reads must not cancel
        // the run's settlement.
        const result = await this.ctx.remote.pluginManager.installBundle(spec, {
            enabled: subject.selection ?? false, requestId, registry,
            ...subject.saveExact === undefined ? {} : { saveExact: subject.saveExact },
            ...approvedBuilds === undefined ? {} : { approvedBuilds: [...approvedBuilds] },
        });
        if (this.disposed || this.request !== request)
            return;
        if (!result.ok) {
            request.replyLost = true;
            this.installUncertain('result', result.error.message);
            void this.reconcileInstall();
            return;
        }
        this.settleInstall(result.value);
    }
    /** A recovery call shares the Host's active result; absent results are explicitly unknown. */
    async reconcileInstall() {
        const request = this.request;
        if (request === undefined || !request.replyLost || request.recovering)
            return;
        request.recovering = true;
        const result = await this.ctx.remote.pluginManager.waitForInstall(request.requestId);
        if (this.disposed || this.request !== request)
            return;
        request.recovering = false;
        if (!result.ok) {
            this.installUncertain('result', result.error.message);
            return;
        }
        if (result.value !== null) {
            this.settleInstall(result.value);
            return;
        }
        this.request = undefined;
        this.finishAnalytics('unknown');
        this.patchInstall({ phase: 'unknown', failure: null, runs: settledRuns(this.getSnapshot().install.runs, null) });
        this.notifyHiddenInstall('unknown');
        void this.load();
    }
    settleInstall(result) {
        this.finishAnalytics(result.application === 'failed' ? 'failed' : result.application === 'cancelled' ? 'cancelled' : 'success', result.application === 'failed' ? result.error?.code : undefined, result.bundle);
        const { runs, attempts } = this.getSnapshot().install;
        this.request = undefined;
        // The Host's answer names every registry it asked, whether or not each attempt's announcement arrived.
        const asked = result.registries === undefined
            ? {}
            : { attempts: { registries: result.registries, total: Math.max(attempts?.total ?? 0, result.registries.length) } };
        if (result.application === 'cancelled') {
            this.offerSpecAgain({ kind: 'cancelled', seq: ++this.noticeSeq });
        }
        else if (result.application === 'failed') {
            const packages = result.packageResult;
            this.patchInstall({
                phase: 'failed', runs: settledRuns(runs, packages?.exitCode ?? null),
                failure: failureOf(result.error, packages?.kind, result.pendingBuilds, result.failedAt),
                ...asked,
            });
        }
        else {
            this.patchInstall({
                phase: 'done', runs: settledRuns(runs, 0), failure: null,
                installed: result.bundle ?? null,
                installedVersion: result.version ?? null,
                restartRequired: result.application === 'restart-required',
                ...result.restartReason === undefined ? {} : { restartReason: result.restartReason },
                approvedBuilds: result.approvedBuilds ?? [],
                ...asked,
            });
        }
        const completed = this.getSnapshot().install;
        if (completed.phase === 'done' && completed.subject?.selection !== undefined && result.bundle !== undefined) {
            this.applied({ ok: true, value: result }, result.bundle);
            this.patch({ highlight: result.bundle });
            if (completed.subject.selection)
                this.trackToggle(result.bundle, true);
        }
        const phase = this.getSnapshot().install.phase;
        if (phase === 'done' || phase === 'failed')
            this.notifyHiddenInstall(phase);
        void this.load();
    }
    installUncertain(uncertainty, reason) {
        this.patchInstall({
            phase: this.getSnapshot().install.phase === 'applying' ? 'applying' : 'unconfirmed',
            failure: { reason, uncertainty },
        });
        this.notifyHiddenInstall('unconfirmed');
    }
    /**
     * Allow the install scripts the failed run left pending and run the same
     * spec again. Only the failed screen with pending names offers this.
     */
    async approveBuildsAndRetry() {
        const install = this.getSnapshot().install;
        const pending = install.failure?.pendingBuilds;
        if (install.phase !== 'failed' || install.subject === null || pending === undefined || pending.length === 0)
            return;
        if (this.ctx.get('productAnalytics')?.enabled)
            this.analyticsAttempt = { input: sanitizeInstallInput(install.subject.spec), started: Date.now() };
        await this.startInstall(install.subject, pending);
    }
    /**
     * Leave the check or the failed screen for the spec at once; a Host-owned
     * run is asked to stop and its state waits for the Host's word, since
     * neither a dropped RPC nor a closed connection means pnpm has stopped.
     */
    async cancelInstall() {
        const install = this.getSnapshot().install;
        if (install.phase === 'checking' || install.phase === 'failed' || install.phase === 'unknown') {
            if (install.phase === 'checking')
                this.finishAnalytics('cancelled');
            this.abortInspect();
            this.offerSpecAgain();
            return;
        }
        const request = this.request;
        if ((install.phase !== 'starting' && install.phase !== 'running' && install.phase !== 'unconfirmed') || request === undefined)
            return;
        if (request.cancellation !== undefined && !request.cancellation.waitingForStart)
            return;
        const cancellation = { waitingForStart: false };
        request.cancellation = cancellation;
        await this.sendCancellation(request, cancellation);
    }
    /** A cancellation that overtakes installation is retried after the Host acknowledges that request. */
    async sendCancellation(request, cancellation) {
        const acknowledged = request.acknowledged;
        cancellation.waitingForStart = false;
        this.patchInstall({ phase: 'cancelling', failure: null });
        const result = await this.ctx.remote.pluginManager.cancelInstall(request.requestId);
        if (this.disposed || this.request !== request || request.cancellation !== cancellation)
            return;
        if (!result.ok) {
            cancellation.waitingForStart = !request.acknowledged;
            if (request.acknowledged)
                request.cancellation = undefined;
            this.installUncertain(request.replyLost ? 'result' : 'cancellation', result.error.message);
            return;
        }
        if (result.value.status === 'cancelled') {
            this.finishAnalytics('cancelled');
            this.offerSpecAgain({ kind: 'cancelled', seq: ++this.noticeSeq });
            void this.load();
        }
        else if (result.value.status === 'too-late') {
            request.acknowledged = true;
            request.cancellation = undefined;
            this.patchInstall({ phase: 'applying' });
            this.notifyHiddenInstall('applying');
            void this.reconcileInstall();
        }
        else if (request.replyLost) {
            request.cancellation = undefined;
            this.installUncertain('result', '');
            void this.reconcileInstall();
        }
        else if (!acknowledged && this.getSnapshot().install.phase !== 'applying') {
            if (request.acknowledged) {
                await this.sendCancellation(request, cancellation);
            }
            else {
                cancellation.waitingForStart = true;
                this.installUncertain('acceptance', '');
            }
        }
        else {
            request.cancellation = undefined;
            this.installUncertain('cancellation', '');
        }
    }
    notifyHiddenInstall(outcome) {
        if (!this.getSnapshot().install.open)
            this.patch({ notice: { kind: 'install', outcome, seq: ++this.noticeSeq } });
    }
    /**
     * Release the tracked request and return to editing with its spec retained.
     * A cancellation notice is supplied only after the Host confirms it stopped.
     */
    offerSpecAgain(notice = null) {
        this.request = undefined;
        this.patch({ install: specAgain(this.getSnapshot().install), ...notice === null ? {} : { notice } });
    }
    /**
     * Enable the bundle ready from an install or the installation, then close the dialog and
     * mark it in the list. A refusal toasts and still closes.
     */
    async enableInstalled() {
        const install = this.getSnapshot().install;
        if (install.phase !== 'done' || install.enabling)
            return;
        const name = install.installed;
        this.patchInstall({ enabling: true });
        if (name !== null) {
            const result = await this.ctx.remote.pluginManager.setBundleEnabled(name, true);
            if (this.disposed)
                return;
            this.finishAnalytics(result.ok && result.value.application !== 'failed' ? 'success' : 'failed', result.ok ? result.value.error?.code : result.error.code, name);
            try {
                this.applied(result, name);
                if (result.ok && (result.value.application === 'applied' || result.value.application === 'restart-required'))
                    this.trackToggle(name, true);
            }
            catch (error) {
                this.patch({ notice: failedNotice(error, { packageName: name, action: 'enable' }, ++this.noticeSeq) });
            }
        }
        this.patch({ install: IDLE_INSTALL, highlight: name });
        await this.load();
    }
    /**
     * Run one action under a busy key, turn its failure into the notice, and
     * re-read the Host afterwards whatever happened.
     */
    async run(key, subject, action) {
        if (this.disposed || this.getSnapshot().busy.includes(key))
            return;
        this.patch({ busy: [...this.getSnapshot().busy, key], notice: null });
        try {
            await action();
        }
        catch (error) {
            // `patch` drops the notice after disposal.
            this.patch({ notice: failedNotice(error, subject, ++this.noticeSeq) });
        }
        finally {
            this.patch({ busy: this.getSnapshot().busy.filter(entry => entry !== key) });
        }
        await this.load();
    }
    /**
     * Publish a change's outcome: a refused answer or a change the Host could
     * not apply throws for {@link run} to report; a change that waits for the
     * next start, that a higher layer overrides, or that the Host stopped is
     * said in passing.
     */
    applied(answer, packageName) {
        if (!answer.ok)
            throw new RemoteAnswerError(answer.error.message);
        const result = answer.value;
        switch (result.application) {
            case 'failed':
                throw new RemoteAnswerError(result.error?.diagnostic ?? '', result.error?.code, result.error?.incompatible);
            case 'cancelled':
                this.patch({ notice: { kind: 'cancelled', seq: ++this.noticeSeq } });
                return;
            case 'restart-required':
                this.patch({ notice: { kind: 'restart', packageName, reason: result.restartReason, seq: ++this.noticeSeq } });
                return;
            case 'overridden':
                this.patch({ notice: { kind: 'overridden', packageName, seq: ++this.noticeSeq } });
                return;
            case 'applied':
                return;
        }
    }
    trackToggle(name, enabled, row = false) {
        const bundle = this.getSnapshot().packages.find(pkg => row ? pkg.rows.some(item => item.entryId === name) : pkg.name === name);
        const pluginName = row ? bundle?.rows.find(item => item.entryId === name)?.moduleName : name;
        if (bundle === undefined || pluginName === undefined)
            return;
        this.ctx.get('productAnalytics')?.track('plugin_toggle', {
            plugin_name: pluginName, plugin_type: row ? 'plugin' : 'bundle', is_enabled: enabled, is_builtin: bundle.availability === 'installation',
        });
    }
    finishAnalytics(status, reason, name) {
        const attempt = this.analyticsAttempt;
        this.analyticsAttempt = undefined;
        if (attempt === undefined || this.disposed)
            return;
        const errorReason = status === 'cancelled' ? 'user_cancelled' : status === 'unknown' ? 'unknown_result' : reason;
        this.ctx.get('productAnalytics')?.track('install_plugin_result', {
            input_value: attempt.input, is_success: status === 'success',
            duration: Math.max(0, Date.now() - attempt.started),
            ...errorReason === undefined ? {} : { error_reason: errorReason }, ...name === undefined ? {} : { plugin_name: name },
        });
    }
    patch(next) {
        if (this.disposed)
            return;
        this.store.set({ ...this.getSnapshot(), ...next });
    }
    patchInstall(next) {
        this.patch({ install: { ...this.getSnapshot().install, ...next } });
    }
}
/** What a thrown failure said: a refused answer's reason, else the error's message. */
function reasonOf(error) {
    return error instanceof Error ? error.message : String(error);
}
//# sourceMappingURL=manager-store.js.map