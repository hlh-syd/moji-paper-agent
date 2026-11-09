import { createSnapshotStore } from '@deepseek-ai/dsh-client-store';
import { NPMMIRROR_REGISTRY } from '@deepseek-ai/dsh-plugin-manager/registry';
/**
 * The identity of a bundle detail read: the bundle, its installed version, and the display language.
 * @param name - the bundle's package name.
 * @param version - the installed or offered version, so an updated bundle reads again.
 * @param language - the display language.
 * @returns the key of the read in {@link BundleDetailsState}.
 */
export function bundleDetailKey(name, version, language) {
    return JSON.stringify([name, version ?? null, language]);
}
/** The Host route of images beside bundle detail documents, relative to the application base. */
const DETAIL_FILE_ROUTE = 'api/plugin-manager/detail-file';
function failureOf(failure) {
    return failure.code === 'plugin-manager/detail-unavailable' ? failure.details.reason : 'other';
}
/** Reads bundle detail documents and publishes them to the page. */
export class BundleDetailController {
    ctx;
    base;
    /** Detail reads published to the page. */
    store = createSnapshotStore({});
    /** The latest read of each key, numbered across the controller's lifetime; an older read's answer is dropped. */
    reads = new Map();
    sequence = 0;
    /** npm file CDNs to ask first, from the Host's latest registry comparison: npmmirror when its registry answered fastest. */
    npmFileSources = [];
    disposed = false;
    /**
     * @param ctx - the page plugin's context, whose `remote.pluginManager` namespace answers.
     * @param base - the application base URL that Host routes resolve against.
     */
    constructor(ctx, base) {
        this.ctx = ctx;
        this.base = base;
    }
    /**
     * Read a bundle's document unless the read already ran or runs.
     * @param name - the bundle's package name.
     * @param version - its installed or offered version.
     * @param language - the display language.
     * @param fromNpm - whether the bundle's files are unavailable, so the Host reads its offered version from the npm file CDNs.
     */
    load(name, version, language, fromNpm) {
        if (!this.reads.has(bundleDetailKey(name, version, language)))
            void this.read(name, version, language, fromNpm);
    }
    /**
     * Read a bundle's document again.
     * @param name - the bundle's package name.
     * @param version - its installed or offered version.
     * @param language - the display language.
     * @param fromNpm - whether the Host reads the bundle from the npm file CDNs.
     */
    retry(name, version, language, fromNpm) {
        void this.read(name, version, language, fromNpm);
    }
    /**
     * The URL of an image beside a bundle's document.
     * @param name - the bundle's package name.
     * @param document - the document's path inside the bundle.
     * @param destination - the image destination as the document writes it.
     * @param fromNpm - whether the Host reads the bundle from the npm file CDNs.
     * @returns the authenticated Host URL, or undefined for an absolute, protocol-relative, or fragment destination.
     */
    image(name, document, destination, fromNpm) {
        if (/^(?:[a-z][a-z\d+.-]*:|\/\/|#)/iu.test(destination))
            return undefined;
        const sources = fromNpm ? this.npmFileSources : [];
        const query = new URLSearchParams({ bundle: name, document, path: destination, ...sources.length > 0 ? { sources: sources.join(',') } : {} });
        return new URL(`${DETAIL_FILE_ROUTE}?${query.toString()}`, this.base()).href;
    }
    /** Invalidate reads while retaining settled documents until their replacements arrive. */
    reset() {
        this.reads.clear();
        this.store.set(Object.fromEntries(Object.entries(this.store.getSnapshot()).filter(([, value]) => value.status === 'ready')));
    }
    /** Stop publishing late settlements. */
    dispose() {
        this.disposed = true;
    }
    async read(name, version, language, fromNpm) {
        const key = bundleDetailKey(name, version, language);
        const read = ++this.sequence;
        this.reads.set(key, read);
        if (this.store.getSnapshot()[key]?.status !== 'ready')
            this.publish(key, { status: 'loading' });
        // The remote is captured before any wait: the plugin's context may be disposed while the comparison runs.
        const { remote } = this.ctx;
        const current = () => !this.disposed && this.reads.get(key) === read;
        // Only a bundle read from the CDNs waits for the registry comparison.
        if (fromNpm) {
            const fastest = await remote.pluginRegistryProbe.fastest();
            // A retry or reset started while the comparison ran settles the key instead.
            if (!current())
                return;
            this.npmFileSources = fastest.ok && fastest.value === NPMMIRROR_REGISTRY ? ['npmmirror'] : [];
        }
        const answer = await remote.pluginManager.readBundleDetail(name, language, { npmFileSources: fromNpm ? this.npmFileSources : [] });
        if (current())
            this.publish(key, answer.ok ? { status: 'ready', detail: answer.value } : { status: 'error', failure: failureOf(answer.error) });
    }
    publish(key, state) {
        this.store.set({ ...this.store.getSnapshot(), [key]: state });
    }
}
//# sourceMappingURL=detail-store.js.map