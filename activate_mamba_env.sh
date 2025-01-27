export MAMBA_EXE='/home/ann/y/micromamba';
export MAMBA_ROOT_PREFIX='/root/micromamba';
eval "$("$MAMBA_EXE" shell hook --shell bash --root-prefix "$MAMBA_ROOT_PREFIX" 2> /dev/null)"
