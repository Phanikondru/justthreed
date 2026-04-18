// Import Mockup Passes — creates a new PSD with each Blender render pass
// dropped in as its own layer. No grouping, no smart objects, no tricks.
// You build the mockup from the layers manually like a pro.
//
// Usage:
//   Photoshop -> File -> Scripts -> Browse... -> pick this file
//   Then select the "renders" folder in the dialog.

#target photoshop

(function () {
    var CANVAS_W = 3840;
    var CANVAS_H = 2160;
    var CANVAS_RES = 72;

    function findLatestFile(folder, prefix, exts) {
        var matches = [];
        var files = folder.getFiles();
        for (var i = 0; i < files.length; i++) {
            var f = files[i];
            if (!(f instanceof File)) continue;
            var name = decodeURI(f.name).toLowerCase();
            if (name.indexOf(prefix.toLowerCase()) !== 0) continue;
            for (var j = 0; j < exts.length; j++) {
                var ext = "." + exts[j].toLowerCase();
                if (name.lastIndexOf(ext) === name.length - ext.length) {
                    matches.push(f);
                    break;
                }
            }
        }
        if (matches.length === 0) return null;
        matches.sort(function (a, b) { return a.name < b.name ? 1 : -1; });
        return matches[0];
    }

    function placeAndRasterize(file, layerName) {
        var desc = new ActionDescriptor();
        desc.putPath(charIDToTypeID("null"), file);
        desc.putEnumerated(charIDToTypeID("FTcs"), charIDToTypeID("QCSt"), charIDToTypeID("Qcsa"));
        executeAction(charIDToTypeID("Plc "), desc, DialogModes.NO);
        try { app.activeDocument.activeLayer.rasterize(RasterizeType.ENTIRELAYER); } catch (e) {}
        app.activeDocument.activeLayer.name = layerName;
        return app.activeDocument.activeLayer;
    }

    var rendersFolder = Folder.selectDialog(
        "Select the 'renders' folder with beauty / shadow / reflections / screen_mask files"
    );
    if (!rendersFolder) return;

    var beauty      = findLatestFile(rendersFolder, "beauty_",      ["tif", "tiff"]);
    var shadow      = findLatestFile(rendersFolder, "shadow_",      ["png"]);
    var reflections = findLatestFile(rendersFolder, "reflections_", ["png"]);
    var screenMask  = findLatestFile(rendersFolder, "screen_mask_", ["png"]);

    var missing = [];
    if (!beauty)      missing.push("beauty_####.tif");
    if (!shadow)      missing.push("shadow_####.png");
    if (!reflections) missing.push("reflections_####.png");
    if (!screenMask)  missing.push("screen_mask_####.png");
    if (missing.length) {
        alert("Missing files in " + rendersFolder.fsName + ":\n\n" + missing.join("\n"));
        return;
    }

    var prevDialogs = app.displayDialogs;
    app.displayDialogs = DialogModes.NO;

    var doc = app.documents.add(
        new UnitValue(CANVAS_W, "px"),
        new UnitValue(CANVAS_H, "px"),
        CANVAS_RES,
        "Mockup Passes",
        NewDocumentMode.RGB,
        DocumentFill.TRANSPARENT,
        1.0,
        BitsPerChannelType.SIXTEEN
    );

    // Drop each pass in bottom-up — each new place stacks above the previous.
    var shadowLayer = placeAndRasterize(shadow, "Shadow");
    shadowLayer.blendMode = BlendMode.MULTIPLY;

    placeAndRasterize(beauty, "Phone Body");

    var reflLayer = placeAndRasterize(reflections, "Reflections");
    reflLayer.blendMode = BlendMode.SCREEN;
    reflLayer.opacity = 40;

    var maskLayer = placeAndRasterize(screenMask, "Screen Mask");
    maskLayer.visible = false;

    app.displayDialogs = prevDialogs;

    alert(
        "Imported 4 layers:\n" +
        "  Screen Mask (hidden)    — Cmd-click thumbnail to load selection\n" +
        "  Reflections             — Screen blend @ 40%\n" +
        "  Phone Body              — the hero\n" +
        "  Shadow                  — Multiply blend\n" +
        "\nAdd your own Background and UI Smart Object from here."
    );
})();
