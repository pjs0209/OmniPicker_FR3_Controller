# Third-party assets

This repository includes upstream model assets and browser libraries for local rendering. Their original notices and terms remain applicable. This repository does not grant additional rights to third-party assets or to the external manufacturer SDKs.

## FAIRINO FR3 CAD / URDF

- Source: https://github.com/FAIR-INNOVATION/frcobot_ros2
- Pinned commit: `fcf0c7f0d60d949d8a9a4238f929a44d07f60379`
- Included paths: `omnipicker_ui/static/models/FR3WMS/` and `FR3WML/`
- The upstream `fairino_description/package.xml` at this revision contains `TODO: License declaration`; no blanket open-source license is asserted here for these assets. Refer to the upstream owner for their applicable terms.

## AgiBot OmniPicker model

- Source: https://github.com/AgibotTech/genie_sim_robot_model
- Pinned commit: `6d5b2159806ae0e64b432b638e9ee288d8bddc8f`
- [Original license](omnipicker_ui/static/models/OmniPicker/LICENSE)
- [Original third-party notices](omnipicker_ui/static/models/OmniPicker/THIRD_PARTY_NOTICES)
- Collada source meshes are preserved; STL conversions are used by the viewer.

## Three.js

- Version: `0.180.0`
- Source: https://github.com/mrdoob/three.js
- [Original MIT license](omnipicker_ui/static/vendor/THREE-LICENSE)

The pinned URLs, original file sizes and SHA-256 digests are recorded in [sources.json](omnipicker_ui/static/models/sources.json). Manufacturer SDK implementation files and binaries are not vendored.
