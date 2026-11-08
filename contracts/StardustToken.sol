// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title StardustToken ($DUST)
 * @notice Fixed-supply utility & creator fuel token for OrionOS and Forge.
 *         - Hard cap: 1,000,000,000 DUST (18 decimals, zero post-genesis minting)
 *         - Deflationary burn sinks for AI compute & mod royalties
 *         - Built-in Founder 4-Tier Cliff + Linear Vesting schedule
 *         - Built-in Phase 3 Merkle Airdrop claim (50% immediate, 50% 6-month linear)
 */
contract StardustToken {
    string public constant name = "Orion Stardust";
    string public constant symbol = "DUST";
    uint8 public constant decimals = 18;

    uint256 public constant INITIAL_SUPPLY = 1_000_000_000 * 1e18;
    uint256 public totalSupply;
    uint256 public totalBurned;

    address public immutable governanceMultisig;
    bytes32 public merkleRoot;
    uint256 public immutable genesisTimestamp;

    // Vesting constants for Founder allocation (150,000,000 DUST)
    uint256 public constant FOUNDER_TOTAL = 150_000_000 * 1e18;
    uint256 public constant FOUNDER_RECOUP_TRANCHE = 15_000_000 * 1e18; // 10% of Founder allocation
    uint256 public constant CLIFF_DURATION = 365 days;
    uint256 public constant VESTING_DURATION = 1095 days; // 36 months linear after 12m cliff

    address public immutable founderBeneficiary;
    uint256 public founderClaimed;
    bool public liquidityGatePassed;

    struct AirdropVest {
        uint256 totalAllocated;
        uint256 claimed;
        uint256 initialClaimTime;
    }

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    mapping(address => AirdropVest) public airdropLedger;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event Burned(address indexed burner, uint256 amount, string reason);
    event MerkleRootUpdated(bytes32 indexed newRoot);
    event LiquidityGateOpened(uint256 timestamp);
    event FounderMilestoneClaimed(address indexed beneficiary, uint256 amount);
    event AirdropClaimed(address indexed account, uint256 amount);

    modifier onlyMultisig() {
        require(msg.sender == governanceMultisig, "DUST: caller is not boardroom/multisig");
        _;
    }

    constructor(
        address _governanceMultisig,
        address _creatorRewardsVault,
        address _computeTreasuryVault,
        address _founderBeneficiary,
        address _liquidityVault
    ) {
        require(_governanceMultisig != address(0), "DUST: zero multisig");
        require(_founderBeneficiary != address(0), "DUST: zero founder");

        governanceMultisig = _governanceMultisig;
        founderBeneficiary = _founderBeneficiary;
        genesisTimestamp = block.timestamp;

        // 1. Creator & Modder Rewards (40% = 400M DUST)
        _mint(_creatorRewardsVault, 400_000_000 * 1e18);
        // 2. Compute & Ecosystem Treasury (25% = 250M DUST)
        _mint(_computeTreasuryVault, 250_000_000 * 1e18);
        // 3. Founder Vesting Reserve held by contract (15% = 150M DUST)
        _mint(address(this), FOUNDER_TOTAL);
        // 4. Community Airdrop Reserve held by contract (10% = 100M DUST)
        _mint(address(this), 100_000_000 * 1e18);
        // 5. Liquidity & Exchange Reserves (10% = 100M DUST)
        _mint(_liquidityVault, 100_000_000 * 1e18);

        assert(totalSupply == INITIAL_SUPPLY);
    }

    function _mint(address to, uint256 amount) internal {
        totalSupply += amount;
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 currentAllowance = allowance[from][msg.sender];
        if (currentAllowance != type(uint256).max) {
            require(currentAllowance >= amount, "DUST: insufficient allowance");
            unchecked {
                allowance[from][msg.sender] = currentAllowance - amount;
            }
        }
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) internal {
        require(from != address(0) && to != address(0), "DUST: zero address");
        uint256 fromBal = balanceOf[from];
        require(fromBal >= amount, "DUST: balance too low");
        unchecked {
            balanceOf[from] = fromBal - amount;
            balanceOf[to] += amount;
        }
        emit Transfer(from, to, amount);
    }

    /**
     * @notice Permanently burns DUST from caller's balance (Compute & Royalty Burn Sink).
     */
    function burn(uint256 amount, string calldata reason) external {
        uint256 bal = balanceOf[msg.sender];
        require(bal >= amount, "DUST: burn exceeds balance");
        unchecked {
            balanceOf[msg.sender] = bal - amount;
            totalSupply -= amount;
            totalBurned += amount;
        }
        emit Transfer(msg.sender, address(0), amount);
        emit Burned(msg.sender, amount, reason);
    }

    // =========================================================================
    // FOUNDER 4-TIER SAFETY NET & VESTING SCHEDULE
    // =========================================================================
    function setLiquidityGatePassed() external onlyMultisig {
        liquidityGatePassed = true;
        emit LiquidityGateOpened(block.timestamp);
    }

    function founderVestedAmount() public view returns (uint256) {
        uint256 vested = 0;
        if (liquidityGatePassed) {
            vested += FOUNDER_RECOUP_TRANCHE;
        }
        uint256 elapsed = block.timestamp - genesisTimestamp;
        if (elapsed >= CLIFF_DURATION) {
            uint256 postCliff = elapsed - CLIFF_DURATION;
            uint256 linearPool = FOUNDER_TOTAL - FOUNDER_RECOUP_TRANCHE; // 135,000,000 DUST
            if (postCliff >= VESTING_DURATION) {
                vested += linearPool;
            } else {
                vested += (linearPool * postCliff) / VESTING_DURATION;
            }
        }
        return vested;
    }

    function claimFounderVested() external {
        require(msg.sender == founderBeneficiary, "DUST: only founder");
        uint256 vested = founderVestedAmount();
        require(vested > founderClaimed, "DUST: nothing new vested");
        uint256 claimable = vested - founderClaimed;
        founderClaimed = vested;
        _transfer(address(this), founderBeneficiary, claimable);
        emit FounderMilestoneClaimed(founderBeneficiary, claimable);
    }

    // =========================================================================
    // MERKLE AIRDROP (50% IMMEDIATE FOR FORGE, 50% 6-MONTH LINEAR)
    // =========================================================================
    function setMerkleRoot(bytes32 _root) external onlyMultisig {
        merkleRoot = _root;
        emit MerkleRootUpdated(_root);
    }

    function claimAirdrop(uint256 totalAllocation, bytes32[] calldata proof) external {
        AirdropVest storage vest = airdropLedger[msg.sender];
        if (vest.totalAllocated == 0) {
            require(merkleRoot != bytes32(0), "DUST: airdrop not active");
            bytes32 leaf = keccak256(abi.encodePacked(msg.sender, totalAllocation));
            require(_verifyMerkle(proof, merkleRoot, leaf), "DUST: invalid merkle proof");
            vest.totalAllocated = totalAllocation;
            vest.initialClaimTime = block.timestamp;
        }

        uint256 unlocked = totalAllocation / 2; // 50% immediate TGE unlock
        uint256 elapsed = block.timestamp - vest.initialClaimTime;
        uint256 sixMonths = 180 days;
        uint256 secondHalf = totalAllocation - unlocked;
        if (elapsed >= sixMonths) {
            unlocked += secondHalf;
        } else {
            unlocked += (secondHalf * elapsed) / sixMonths;
        }

        require(unlocked > vest.claimed, "DUST: nothing claimable yet");
        uint256 payout = unlocked - vest.claimed;
        vest.claimed = unlocked;
        _transfer(address(this), msg.sender, payout);
        emit AirdropClaimed(msg.sender, payout);
    }

    function _verifyMerkle(bytes32[] calldata proof, bytes32 root, bytes32 leaf) internal pure returns (bool) {
        bytes32 computed = leaf;
        for (uint256 i = 0; i < proof.length; i++) {
            bytes32 el = proof[i];
            if (computed <= el) {
                computed = keccak256(abi.encodePacked(computed, el));
            } else {
                computed = keccak256(abi.encodePacked(el, computed));
            }
        }
        return computed == root;
    }
}
