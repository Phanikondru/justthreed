// Import Mockup Passes — creates a new PSD with each Blender render pass
// dropped in as its own layer. No grouping, no smart objects, no tricks.
// You build the mockup from the layers manually like a pro.
//
// Expected files in the selected folder (transparent background):
//   phone_body.tif|.png    — full phone, screen area pure black (required, 16-bit TIFF preferred)
//   screen_mask.png        — solid white display area, notch transparent (required)
//   phone_shadow.png       — soft contact shadow only, phone hidden (required)
//   phone_reflections.png  — specular highlights only (optional)
//
// Trailing version suffixes are fine: phone_body_v02.png, phone_body_001.png, etc.
// The script picks the latest match alphabetically.
//
// Usage:
//   Photoshop -> File -> Scripts -> Browse... -> pick this file
//   Then select the "renders" folder in the dialog.

#target photoshop

(function () {
    var CANVAS_W = 3840;
    var CANVAS_H = 2160;
    var CANVAS_RES = 72;

    function findLatestFile(folder, baseName, exts) {
        var matches = [];
        var files = folder.getFiles();
        var base = baseName.toLowerCase();
        for (var i = 0; i < files.length; i++) {
            var f = files[i];
            if (!(f instanceof File)) continue;
            var name = decodeURI(f.name).toLowerCase();
            // Match either exact "<base>.<ext>" or "<base>_*.<ext>" (versioned)
            if (name.indexOf(base) !== 0) continue;
            var afterBase = name.charAt(base.length);
            if (afterBase !== "." && afterBase !== "_") continue;
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
        "Select the 'renders' folder with phone_body / screen_mask / phone_shadow / phone_reflections"
    );
    if (!rendersFolder) return;

    var phoneBody   = findLatestFile(rendersFolder, "phone_body",        ["tif", "tiff", "png"]);
    var screenMask  = findLatestFile(rendersFolder, "screen_mask",       ["png"]);
    var shadow      = findLatestFile(rendersFolder, "phone_shadow",      ["png"]);
    var reflections = findLatestFile(rendersFolder, "phone_reflections", ["png"]); // optional

    var missing = [];
    if (!phoneBody)  missing.push("phone_body.(tif|png)");
    if (!screenMask) missing.push("screen_mask.png");
    if (!shadow)     missing.push("phone_shadow.png");
    if (missing.length) {
        alert("Missing required files in " + rendersFolder.fsName + ":\n\n" + missing.join("\n"));
        return;
    }

    var prevDialogs = app.displayDialogs;
    app.displayDialogs = DialogModes.NO;

    app.documents.add(
        new UnitValue(CANVAS_W, "px"),
        new UnitValue(CANVAS_H, "px"),
        CANVAS_RES,
        "Mockup Passes",
        NewDocumentMode.RGB,
        DocumentFill.TRANSPARENT,
        1.0,
        BitsPerChannelType.SIXTEEN
    );

    // Stack bottom-up: each new place lands above the previous layer.
    var shadowLayer = placeAndRasterize(shadow, "Shadow");
    shadowLayer.blendMode = BlendMode.MULTIPLY;

    placeAndRasterize(phoneBody, "Phone Body");

    if (reflections) {
        var reflLayer = placeAndRasterize(reflections, "Reflections");
        reflLayer.blendMode = BlendMode.SCREEN;
        reflLayer.opacity = 40;
    }

    var maskLayer = placeAndRasterize(screenMask, "Screen Mask");
    maskLayer.visible = false;

    app.displayDialogs = prevDialogs;

    var summary =
        "Imported layers (top -> bottom):\n" +
        "  Screen Mask (hidden)    — Cmd-click thumbnail to load selection\n";
    if (reflections) {
        summary += "  Reflections             — Screen blend @ 40%\n";
    }
    summary +=
        "  Phone Body              — the hero\n" +
        "  Shadow                  — Multiply blend\n" +
        "\nAdd your own Background and UI Smart Object from here.";
    if (!reflections) {
        summary += "\n\n(phone_reflections.png not found — skipped. Optional pass.)";
    }
    alert(summary);
})();
