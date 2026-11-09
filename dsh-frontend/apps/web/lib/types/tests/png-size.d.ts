/**
 * Read the dimensions of a PNG with its mandatory first IHDR chunk.
 * @param png - encoded image bytes.
 * @returns width and height in pixels.
 * @throws when the PNG signature or IHDR chunk is missing.
 */
export declare function pngSize(png: Buffer): readonly [number, number];
//# sourceMappingURL=png-size.d.ts.map