export default {
  ignoreFiles: ["**/*.min.css"],
  rules: {
    "custom-property-pattern": [
      "^cb-[a-z0-9]+(?:-[a-z0-9]+)*$",
      {
        message: "Use the canonical --cb-* design-token namespace."
      }
    ],
    "declaration-property-value-disallowed-list": {
      "/^.*$/": [
        "/#[0-9a-fA-F]{3,8}\\b/",
        "/(^|[\\s,(])(?:white|black|red|green|blue)(?=[\\s,);]|$)/i"
      ]
    },
    "declaration-no-important": [
      true,
      {
        severity: "warning"
      }
    ]
  },
  overrides: [
    {
      files: ["products/reunia/static/css/design-tokens.css"],
      rules: {
        "declaration-property-value-disallowed-list": null
      }
    }
  ]
};
