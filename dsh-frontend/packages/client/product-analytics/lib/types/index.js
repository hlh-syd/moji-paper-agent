var __runInitializers = (this && this.__runInitializers) || function (thisArg, initializers, value) {
    var useValue = arguments.length > 2;
    for (var i = 0; i < initializers.length; i++) {
        value = useValue ? initializers[i].call(thisArg, value) : initializers[i].call(thisArg);
    }
    return useValue ? value : void 0;
};
var __esDecorate = (this && this.__esDecorate) || function (ctor, descriptorIn, decorators, contextIn, initializers, extraInitializers) {
    function accept(f) { if (f !== void 0 && typeof f !== "function") throw new TypeError("Function expected"); return f; }
    var kind = contextIn.kind, key = kind === "getter" ? "get" : kind === "setter" ? "set" : "value";
    var target = !descriptorIn && ctor ? contextIn["static"] ? ctor : ctor.prototype : null;
    var descriptor = descriptorIn || (target ? Object.getOwnPropertyDescriptor(target, contextIn.name) : {});
    var _, done = false;
    for (var i = decorators.length - 1; i >= 0; i--) {
        var context = {};
        for (var p in contextIn) context[p] = p === "access" ? {} : contextIn[p];
        for (var p in contextIn.access) context.access[p] = contextIn.access[p];
        context.addInitializer = function (f) { if (done) throw new TypeError("Cannot add initializers after decoration has completed"); extraInitializers.push(accept(f || null)); };
        var result = (0, decorators[i])(kind === "accessor" ? { get: descriptor.get, set: descriptor.set } : descriptor[key], context);
        if (kind === "accessor") {
            if (result === void 0) continue;
            if (result === null || typeof result !== "object") throw new TypeError("Object expected");
            if (_ = accept(result.get)) descriptor.get = _;
            if (_ = accept(result.set)) descriptor.set = _;
            if (_ = accept(result.init)) initializers.unshift(_);
        }
        else if (_ = accept(result)) {
            if (kind === "field") initializers.unshift(_);
            else descriptor[key] = _;
        }
    }
    if (target) Object.defineProperty(target, contextIn.name, descriptor);
    done = true;
};
import z from '@deepseek-ai/schemastery';
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol';
import { collectEnvironment } from "./environment.js";
let ProductAnalytics = (() => {
    let _classSuper = TypertRemoteService;
    let _instanceExtraInitializers = [];
    let _enabled_decorators;
    let _watchPolicy_decorators;
    let _report_decorators;
    return class ProductAnalytics extends _classSuper {
        static {
            const _metadata = typeof Symbol === "function" && Symbol.metadata ? Object.create(_classSuper[Symbol.metadata] ?? null) : void 0;
            _enabled_decorators = [Remote];
            _watchPolicy_decorators = [Remote({ mode: 'stream' })];
            _report_decorators = [Remote];
            __esDecorate(this, null, _enabled_decorators, { kind: "method", name: "enabled", static: false, private: false, access: { has: obj => "enabled" in obj, get: obj => obj.enabled }, metadata: _metadata }, null, _instanceExtraInitializers);
            __esDecorate(this, null, _watchPolicy_decorators, { kind: "method", name: "watchPolicy", static: false, private: false, access: { has: obj => "watchPolicy" in obj, get: obj => obj.watchPolicy }, metadata: _metadata }, null, _instanceExtraInitializers);
            __esDecorate(this, null, _report_decorators, { kind: "method", name: "report", static: false, private: false, access: { has: obj => "report" in obj, get: obj => obj.report }, metadata: _metadata }, null, _instanceExtraInitializers);
            if (_metadata) Object.defineProperty(this, Symbol.metadata, { enumerable: true, configurable: true, writable: true, value: _metadata });
        }
        config = __runInitializers(this, _instanceExtraInitializers);
        static inject = ['deepseekAccount', 'productTelemetry'];
        static Config = z.object({
            enabled: z.boolean().default(true).volatile(), appVersion: z.string(),
            environmentQueryTimeoutMillis: z.number().step(1).min(1).max(2_147_483_647).default(5000),
            environmentQueryMaxOutputBytes: z.number().step(1).min(1).max(2_147_483_647).default(16384),
        });
        active = true;
        environmentLifetime = new AbortController();
        environment;
        listeners = new Set();
        constructor(ctx, config) {
            super(ctx, 'productAnalytics');
            this.config = config;
            ctx.effect(() => async () => {
                this.active = false;
                for (const listener of this.listeners)
                    listener();
                this.environmentLifetime.abort();
                await this.environment;
            });
            ctx.on('loader/volatile-update', () => { for (const listener of this.listeners)
                listener(); });
            ctx.inject(['settings'], (child) => { child.effect(() => child.settings.configure({ auto: false }, ctx.fiber)); });
            ctx.on('session/event', (session, event) => {
                if (event.type !== 'compaction/start')
                    return;
                void this.report({ eventName: 'context_compression', timestamp: Date.now(),
                    attributes: { session_id: session.id, trigger_type: event.data.turn === null ? 'manual' : 'auto' } });
            });
        }
        /**
         * Read the collection policy.
         * @returns whether this Host currently accepts Desktop analytics.
         */
        enabled() { return this.active && this.config.enabled.get(); }
        /**
         * Stream the effective policy initially and after live configuration edits.
         * @param signal - subscriber lifetime.
         * @returns current policy values until cancellation or service disposal.
         */
        async *watchPolicy(signal) {
            let update = Promise.withResolvers();
            const changed = () => { update.resolve(); };
            this.listeners.add(changed);
            signal.addEventListener('abort', changed, { once: true });
            try {
                while (this.active && !signal.aborted) {
                    yield this.enabled();
                    await update.promise;
                    update = Promise.withResolvers();
                }
            }
            finally {
                this.listeners.delete(changed);
                signal.removeEventListener('abort', changed);
            }
        }
        /**
         * Submit selected Desktop fields with cached static environment and fresh identity.
         * The first intake waits for bounded environment queries; concurrent intakes share them.
         * Missing identity is omitted; failed environment fields carry fixed reasons.
         * @param event - typed product event without message contents or credentials.
         * @returns after local submission; no delivery or warehouse acknowledgement.
         */
        async report(event) {
            if (!this.enabled())
                return;
            try {
                this.environment ??= collectEnvironment({
                    timeoutMillis: this.config.environmentQueryTimeoutMillis,
                    maxOutputBytes: this.config.environmentQueryMaxOutputBytes,
                    signal: this.environmentLifetime.signal,
                });
                const [environment, identity] = await Promise.all([
                    this.environment, this.ctx.deepseekAccount.getDeviceIdentity().catch(() => undefined),
                ]);
                if (!this.enabled())
                    return;
                const { missing_fields: missingFields, ...metadata } = environment;
                const missing = Object.entries(missingFields).sort(([a], [b]) => a.localeCompare(b))
                    .map(([field, reason]) => `${field}=${reason}`).join(',');
                this.ctx.productTelemetry.emit({
                    ...event, body: event.eventName,
                    attributes: {
                        ...event.attributes,
                        ...metadata,
                        ...missing === '' ? {} : { missing_fields: missing },
                        ...identity?.deviceId === undefined ? {} : { device_id: identity.deviceId },
                        ...identity?.userId === undefined ? {} : { user_id: identity.userId },
                        ...this.config.appVersion === undefined ? {} : { app_version: this.config.appVersion },
                    },
                });
            }
            catch (error) {
                this.ctx.logger.warn('Product analytics submission failed', error);
            }
        }
    };
})();
/** Authenticated event intake; disabled instances do not inspect identity or accept new events. */
export default ProductAnalytics;
//# sourceMappingURL=index.js.map