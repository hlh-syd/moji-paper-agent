/**
 * Render the install icons of the Web application from the official
 * application tile.
 *
 * A browser installs a Web App only with a raster 192px and a 512px icon, masks
 * the icon it puts on the home screen, and has no manifest support for iOS,
 * which reads `apple-touch-icon` instead. The whale favicon is the bare mark
 * drawn edge to edge: handed to a launcher directly it loses its fins to the
 * mask, and it carries neither the tile nor a background of its own. This
 * script rasterizes the official application tile (`apps/desktop/resources/icon.svg`,
 * the artwork the desktop application ships) into the committed PNGs under
 * `public/`. Every variant crops the tile's drop shadow, which belongs to a
 * desktop window rather than a launcher; the maskable variant additionally
 * scales the whale inside the safe zone a platform mask may crop, leaving the
 * tile's own gradient to fill the icon so no mask meets a seam.
 *
 * The committed PNGs are the output; rerun
 * `pnpm --filter @deepseek-ai/dsh-web-frontend run render:app-icons` after the
 * tile artwork changes.
 * @module render-app-icons
 */
/** Vector source owned by the desktop application; 1104 units square with the tile inset. */
export declare const TILE_SOURCE: string;
/** Directory the generated PNGs are committed to. */
export declare const ICON_OUTPUT_DIRECTORY: string;
/**
 * Fraction of a maskable icon the artwork keeps: the platform may crop the
 * border outside it. The tile's own ink reaches 0.461 of its edge from the
 * centre, so 0.868 is the largest scale a circular mask still shows whole;
 * `pwa-manifest.e2e.ts` checks the committed artwork against that circle.
 */
export declare const MASKABLE_ARTWORK_SCALE = 0.82;
/** Radius a platform mask is guaranteed to show, as a fraction of the icon edge. */
export declare const MASKABLE_SAFE_RADIUS_RATIO = 0.4;
/** One committed install icon: the manifest or link member it serves and its edge in pixels. */
export interface AppIconVariant {
    /** File name inside `public/`. */
    readonly file: string;
    /** Square edge in pixels. */
    readonly size: number;
    /** Which rendering the platform receives. */
    readonly kind: 'tile' | 'maskable';
}
/**
 * Install icons the Web application ships. Chrome requires the 192px and 512px
 * raster pair to offer installation, prefers a maskable icon for the home
 * screen, and iOS reads the touch icon alone.
 */
export declare const APP_ICON_VARIANTS: readonly AppIconVariant[];
/**
 * Rewrite the tile source so the icon canvas is exactly the tile.
 * @param source - complete `icon.svg` document.
 * @returns an SVG document whose 1024-unit square is the tile without its shadow.
 */
export declare function tileDocument(source: string): string;
/**
 * Rewrite the tile source into a maskable icon. The tile keeps its own gradient
 * and extent, so the icon's border is the real tile rather than an extension;
 * only the whale scales into the safe zone a platform mask may crop.
 * @param source - complete `icon.svg` document.
 * @returns an SVG document whose artwork survives a circular mask whole.
 */
export declare function maskableDocument(source: string): string;
/**
 * Render every install icon from the tile source.
 * @param source - complete `icon.svg` document.
 * @returns one PNG per {@link APP_ICON_VARIANTS} entry, in declaration order.
 */
export declare function renderAppIcons(source: string): Promise<readonly {
    variant: AppIconVariant;
    png: Buffer;
}[]>;
//# sourceMappingURL=render-app-icons.d.ts.map