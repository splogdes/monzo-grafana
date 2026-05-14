{
  description = "Monzo → Grafana personal finance dashboard dev environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            uv
            python312

            # C-extension build deps (cffi, cryptography, etc.)
            libffi
            openssl
            pkg-config
            zlib
          ];

          shellHook = ''
            # Pin uv to the nixpkgs Python so it never downloads its own interpreter
            export UV_PYTHON=${pkgs.python312}/bin/python3
            export UV_PYTHON_PREFERENCE=only-system

            # Expose pkg-config paths so cffi / cryptography compile correctly
            export PKG_CONFIG_PATH="${pkgs.libffi.dev}/lib/pkgconfig:${pkgs.openssl.dev}/lib/pkgconfig"
            export LD_LIBRARY_PATH="${pkgs.libffi}/lib:${pkgs.openssl.out}/lib"

            uv sync --quiet
          '';
        };
      });
}
