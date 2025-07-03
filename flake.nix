{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    { devShell.${system} = pkgs.stdenv.mkDerivation rec {
      name = "env";
      buildInputs = with pkgs; [
        python311
        libffi
      ];
   };};
}
