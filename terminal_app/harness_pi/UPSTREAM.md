# Pi upstream pin

```text
package = @earendil-works/pi-coding-agent
version = 0.84.2
repository = https://github.com/earendil-works/pi
directory = packages/coding-agent
npm gitHead = 914cf1472e715297caa30db4b9535d534a9eb718
license = MIT
dist integrity = sha512-l4E+B7hgXKWddRo8bC/eSue2aWZjEgJ9xIpf5p0Og+lq8a2TArCwJ0HCoCPCgaBP/tN4zbYH/wOwvx9pJpeLCA==
local patches = none
```

The earlier `@mariozechner/pi-coding-agent@0.73.1` package is deprecated by its
publisher in favor of this namespace. The current package retains the original
maintainer among its npm maintainers. This namespace transition is explicit and
must be re-audited on any future upgrade.

Install and build automation must use `--ignore-scripts` during dependency
materialization. Founder Alpha does not load global, project-local, remote or
third-party Pi extensions.
