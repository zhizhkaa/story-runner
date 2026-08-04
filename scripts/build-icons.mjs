import { writeFile } from "node:fs/promises";

const iconNames = [
  "check",
  "chevron-down",
  "copy",
  "external-link",
  "eye",
  "link",
  "lock-open",
  "log-in",
  "pencil",
  "play",
  "plus",
  "rotate-ccw",
  "rotate-cw",
  "save",
  "search",
  "send",
  "trash-2",
  "x",
];

const entries = await Promise.all(
  iconNames.map(async (name) => {
    const module = await import(`../node_modules/lucide/dist/esm/icons/${name}.mjs`);
    return [name, module.default];
  }),
);

const runtime = `/* Lucide ${iconNames.length}-icon subset · ISC license */
(() => {
  const icons = ${JSON.stringify(Object.fromEntries(entries))};
  const namespace = "http://www.w3.org/2000/svg";

  function createIcons(root = document) {
    root.querySelectorAll("i[data-lucide]").forEach((placeholder) => {
      const nodes = icons[placeholder.dataset.lucide];
      if (!nodes) return;

      const svg = document.createElementNS(namespace, "svg");
      for (const attribute of placeholder.attributes) {
        if (attribute.name !== "data-lucide") svg.setAttribute(attribute.name, attribute.value);
      }
      svg.setAttribute("xmlns", namespace);
      svg.setAttribute("viewBox", "0 0 24 24");
      svg.setAttribute("fill", "none");
      svg.setAttribute("stroke", "currentColor");
      svg.setAttribute("stroke-width", "2");
      svg.setAttribute("stroke-linecap", "round");
      svg.setAttribute("stroke-linejoin", "round");
      svg.setAttribute("focusable", "false");

      for (const [tag, attributes] of nodes) {
        const child = document.createElementNS(namespace, tag);
        for (const [name, value] of Object.entries(attributes)) child.setAttribute(name, value);
        svg.append(child);
      }
      placeholder.replaceWith(svg);
    });
  }

  window.storyRunnerIcons = { createIcons };
})();
`;

const output = new URL("../stories/static/stories/icons.js", import.meta.url);
await writeFile(output, runtime);
console.log(`Built ${iconNames.length} Lucide icons → stories/static/stories/icons.js`);
