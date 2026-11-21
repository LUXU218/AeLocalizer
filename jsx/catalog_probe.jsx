// 目录探针:在 AE 内导出 app.effects 全量目录(displayName/matchName/category),
// 供「自动识别与适配」的运行时交叉校验(registry_layout)当对照面。
// 只读,不碰工程、不碰插件文件。经 AfterFX.exe -r 转交执行。
// 适配 After-Effects-Plugin-Localizer 的 catalog_probe 思路(MIT,
// Copyright (c) 2026 verse);本版本只读目录、不做改名试验。
(function () {
    var outputPath = @@OUTPUT@@;
    function quoted(s) {
        return '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"')
            .replace(/\r/g, "\\r").replace(/\n/g, "\\n").replace(/\t/g, "\\t")
            .replace(/[\x00-\x1f]/g, function (c) {
                return "\\u" + ("0000" + c.charCodeAt(0).toString(16)).slice(-4);
            }) + '"';
    }
    var report = { schemaVersion: 1, aeVersion: String(app.version), catalog: [], error: null };
    try {
        var list = app.effects;
        for (var i = 0; i < list.length; i++) {
            report.catalog.push({
                displayName: String(list[i].displayName),
                matchName: String(list[i].matchName),
                category: String(list[i].category),
                version: String(list[i].version)
            });
        }
        report.effectCount = report.catalog.length;
        report.rendering = app.project.renderQueue.rendering;
    } catch (err) {
        report.error = String(err);
    }
    var parts = [];
    for (var j = 0; j < report.catalog.length; j++) {
        var row = report.catalog[j];
        parts.push('{"displayName":' + quoted(row.displayName)
            + ',"matchName":' + quoted(row.matchName)
            + ',"category":' + quoted(row.category)
            + ',"version":' + quoted(row.version) + '}');
    }
    var payload = '{"schemaVersion":1,"aeVersion":' + quoted(report.aeVersion)
        + ',"effectCount":' + report.catalog.length
        + ',"rendering":' + (report.rendering ? "true" : "false")
        + ',"error":' + (report.error ? quoted(report.error) : "null")
        + ',"catalog":[' + parts.join(",") + ']}';
    var file = new File(outputPath);
    file.encoding = "UTF-8";
    if (!file.open("w")) {
        $.writeln("AELOCALIZER_CATALOG " + payload);
        alert("AeLocalizer 目录探针无法写结果文件: " + file.error);
        return;
    }
    file.write(payload);
    file.close();
})();
