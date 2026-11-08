// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IStardustToken {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function burn(uint256 amount, string calldata reason) external;
}

/**
 * @title ForgeSplitter
 * @notice On-chain lineage registry, automatic mod remix royalty splitter (60/25/10/5),
 *         and cross-game creator tip router (97/3) for OrionOS Forge.
 */
contract ForgeSplitter {
    IStardustToken public immutable dust;

    uint256 public constant BPS_DENOMINATOR = 10_000;
    uint256 public constant REMIXER_BPS = 6000;      // 60%
    uint256 public constant ORIGINAL_BPS = 2500;     // 25%
    uint256 public constant ASSET_BPS = 1000;        // 10%
    uint256 public constant ROYALTY_BURN_BPS = 500;  // 5%

    uint256 public constant TIP_CREATOR_BPS = 9700;  // 97%
    uint256 public constant TIP_BURN_BPS = 300;      // 3%

    struct ModLineage {
        address remixer;
        address originalAuthor;
        address assetAuthor;
        string gameId;
        bool exists;
        uint256 totalVolumeDust;
    }

    mapping(bytes32 => ModLineage) public mods;

    event ModRegistered(
        bytes32 indexed modKey,
        address indexed remixer,
        address indexed originalAuthor,
        address assetAuthor,
        string gameId
    );

    event ModRoyaltySplit(
        bytes32 indexed modKey,
        address indexed buyer,
        uint256 totalAmount,
        uint256 remixerAmount,
        uint256 originalAmount,
        uint256 assetAmount,
        uint256 burnedAmount
    );

    event CreatorTipped(
        address indexed sender,
        address indexed creator,
        string gameProject,
        uint256 creatorAmount,
        uint256 burnedAmount,
        string message
    );

    constructor(address _dustToken) {
        require(_dustToken != address(0), "ForgeSplitter: zero token");
        dust = IStardustToken(_dustToken);
    }

    /**
     * @notice Registers a mod or forked remix on-chain so royalties route automatically.
     */
    function registerMod(
        bytes32 modKey,
        address originalAuthor,
        address assetAuthor,
        string calldata gameId
    ) external {
        require(!mods[modKey].exists, "ForgeSplitter: mod already registered");
        address orig = originalAuthor == address(0) ? msg.sender : originalAuthor;
        address asset = assetAuthor == address(0) ? orig : assetAuthor;

        mods[modKey] = ModLineage({
            remixer: msg.sender,
            originalAuthor: orig,
            assetAuthor: asset,
            gameId: gameId,
            exists: true,
            totalVolumeDust: 0
        });

        emit ModRegistered(modKey, msg.sender, orig, asset, gameId);
    }

    /**
     * @notice Splits a mod purchase or remix fee: 60% Remixer, 25% Original, 10% Asset, 5% Burn.
     */
    function payModRoyalty(bytes32 modKey, uint256 amount) external {
        require(amount > 0, "ForgeSplitter: zero amount");
        ModLineage storage m = mods[modKey];
        require(m.exists, "ForgeSplitter: mod not found");

        require(dust.transferFrom(msg.sender, address(this), amount), "ForgeSplitter: pull failed");

        uint256 remixerCut = (amount * REMIXER_BPS) / BPS_DENOMINATOR;
        uint256 originalCut = (amount * ORIGINAL_BPS) / BPS_DENOMINATOR;
        uint256 assetCut = (amount * ASSET_BPS) / BPS_DENOMINATOR;
        uint256 burnCut = amount - (remixerCut + originalCut + assetCut);

        m.totalVolumeDust += amount;

        require(dust.transfer(m.remixer, remixerCut), "ForgeSplitter: remixer transfer failed");
        require(dust.transfer(m.originalAuthor, originalCut), "ForgeSplitter: original transfer failed");
        require(dust.transfer(m.assetAuthor, assetCut), "ForgeSplitter: asset transfer failed");
        if (burnCut > 0) {
            dust.burn(burnCut, "forge_royalty_5pct");
        }

        emit ModRoyaltySplit(modKey, msg.sender, amount, remixerCut, originalCut, assetCut, burnCut);
    }

    /**
     * @notice Direct creator tipping across any game project: 97% to creator, 3% burned.
     */
    function tipCreator(
        address creator,
        uint256 amount,
        string calldata gameProject,
        string calldata message
    ) external {
        require(creator != address(0) && creator != msg.sender, "ForgeSplitter: invalid creator");
        require(amount > 0, "ForgeSplitter: zero tip");

        require(dust.transferFrom(msg.sender, address(this), amount), "ForgeSplitter: pull failed");

        uint256 creatorCut = (amount * TIP_CREATOR_BPS) / BPS_DENOMINATOR;
        uint256 burnCut = amount - creatorCut;

        require(dust.transfer(creator, creatorCut), "ForgeSplitter: tip transfer failed");
        if (burnCut > 0) {
            dust.burn(burnCut, "creator_tip_3pct");
        }

        emit CreatorTipped(msg.sender, creator, gameProject, creatorCut, burnCut, message);
    }
}
