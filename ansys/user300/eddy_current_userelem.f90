subroutine USERELEM(elId, matId, keyMtx, lumpm, nDim, nNodes, &
                    Nodes, nIntPnts, nUsrDof, kEStress, &
                    keyAnsMat, keySym, nKeyOpt, KeyOpt, &
                    temper, temperB, tRef, kTherm, &
                    nPress, Press, kPress, nReal, RealConst, &
                    nSaveVars, saveVars, xRef, xCur, &
                    TotValDofs, IncValDofs, ItrValDofs, &
                    VelValDofs, AccValDofs, &
                    kfstps, nlgeom, nrkey, outkey, elPrint, iott, &
                    keyHisUpd, ldstep, isubst, ieqitr, timval, &
                    keyEleErr, keyEleCnv, &
                    eStiff, eMass, eDamp, eSStiff, &
                    fExt, fInt, elVol, elMass, elCG, &
                    nRsltBsc, RsltBsc, nRsltVar, RsltVar, &
                    nElEng, elEnergy)
  implicit none
!DEC$ ATTRIBUTES DLLEXPORT :: USERELEM

  integer, intent(in) :: elId, matId, lumpm, nDim, nNodes, nIntPnts, nUsrDof
  integer, intent(in) :: kEStress, keyAnsMat, keySym, nKeyOpt, kTherm
  integer, intent(in) :: nPress, kPress, nReal, nSaveVars
  integer, intent(in) :: kfstps, nlgeom, nrkey, outkey, elPrint, iott
  integer, intent(in) :: keyHisUpd, ldstep, isubst, ieqitr
  integer, intent(inout) :: keyEleErr, keyEleCnv
  integer, intent(in) :: nRsltBsc, nRsltVar, nElEng
  integer, intent(in) :: keyMtx(10), Nodes(nNodes), KeyOpt(nKeyOpt)

  real(8), intent(in) :: temper(nNodes), temperB(nNodes), tRef
  real(8), intent(in) :: Press(nPress), RealConst(nReal)
  real(8), intent(inout) :: saveVars(nSaveVars)
  real(8), intent(in) :: xRef(nDim, nNodes), xCur(nDim, nNodes)
  real(8), intent(in) :: TotValDofs(nUsrDof), IncValDofs(nUsrDof)
  real(8), intent(in) :: ItrValDofs(nUsrDof), VelValDofs(nUsrDof)
  real(8), intent(in) :: AccValDofs(nUsrDof), timval

  real(8), intent(inout) :: eStiff(nUsrDof, nUsrDof), eMass(nUsrDof, nUsrDof)
  real(8), intent(inout) :: eDamp(nUsrDof, nUsrDof), eSStiff(nUsrDof, nUsrDof)
  real(8), intent(out) :: fExt(nUsrDof), fInt(nUsrDof)
  real(8), intent(out) :: elVol, elMass, elCG(3)
  real(8), intent(out) :: RsltBsc(nRsltBsc), RsltVar(nRsltVar), elEnergy(nElEng)

  integer :: i, comp, i_node1, i_node2
  integer :: damper_type, tangent_mode, active_direction, result_comp
  real(8) :: fmax, vcr, fc, vs, damping_c, alpha
  real(8) :: rel_disp_vec(3), rel_vel_vec(3)
  real(8) :: ceff_vec(3), force_vec(3), dfdv_vec(3), tangent_vec(3)
  real(8) :: rel_vel, abs_v, vel_sq, tanh_arg, tanh_val, vreg
  real(8) :: v_floor, active_flag
  real(8), parameter :: small = 1.0d-20
  integer, parameter :: DAMPER_EDDY = 1
  integer, parameter :: DAMPER_FRICTION = 2
  integer, parameter :: DAMPER_VISCOUS = 3
  integer, parameter :: TANGENT_NONNEGATIVE = 1
  integer, parameter :: TANGENT_RAW = 2
  integer, parameter :: TANGENT_SECANT = 3
  integer, parameter :: TANGENT_FORCE_ONLY = 4
  integer, parameter :: TANGENT_DISABLED = 5
  integer, parameter :: TANGENT_COMBIN37_EQUIV = 6

  keyEleErr = 0
  keyEleCnv = 1

  do i = 1, nUsrDof
    fExt(i) = 0.0d0
    fInt(i) = 0.0d0
  end do

  do i = 1, nUsrDof
    eStiff(1:nUsrDof, i) = 0.0d0
    eMass(1:nUsrDof, i) = 0.0d0
    eDamp(1:nUsrDof, i) = 0.0d0
    eSStiff(1:nUsrDof, i) = 0.0d0
  end do

  elVol = 0.0d0
  elMass = 0.0d0
  elCG(1:3) = 0.0d0
  if (nElEng > 0) elEnergy(1:nElEng) = 0.0d0
  if (nRsltBsc > 0) RsltBsc(1:nRsltBsc) = 0.0d0
  if (nRsltVar > 0) RsltVar(1:nRsltVar) = 0.0d0

  if (nNodes /= 2) then
    keyEleErr = 1
    keyEleCnv = 0
    return
  end if
  if (nUsrDof /= 6) then
    keyEleErr = 1
    keyEleCnv = 0
    return
  end if
  if (nReal < 2) then
    keyEleErr = 1
    keyEleCnv = 0
    return
  end if
  if (RealConst(2) <= small) then
    keyEleErr = 1
    keyEleCnv = 0
    return
  end if

  damper_type = DAMPER_EDDY
  if (nKeyOpt >= 1) then
    if (KeyOpt(1) > 0) damper_type = KeyOpt(1)
  end if

  tangent_mode = TANGENT_NONNEGATIVE
  if (nKeyOpt >= 2) then
    if (KeyOpt(2) > 0) tangent_mode = KeyOpt(2)
  end if

  active_direction = 0
  if (nKeyOpt >= 3) then
    active_direction = KeyOpt(3)
    if (active_direction < 1 .or. active_direction > 3) active_direction = 0
  end if
  if (active_direction == 0 .and. nReal >= 4) then
    ! 当前 MAPDL v242 会在调用用户子程序前拒绝非零 KEYOPT(3)，
    ! 因此生产命令流通过 RealConst(4) 传递作用方向：
    ! 1=UX、2=UY、3=UZ、0=全部方向。
    ! 保留上方 KEYOPT(3) 读取逻辑，仅供允许该参数的接口使用。
    active_direction = nint(RealConst(4))
    if (active_direction < 1 .or. active_direction > 3) active_direction = 0
  end if

  v_floor = 0.0d0
  if (nReal >= 3) then
    v_floor = RealConst(3)
    v_floor = max(v_floor, 0.0d0)
  end if

  active_flag = 1.0d0
  if (nReal >= 5) then
    if (damper_type /= DAMPER_FRICTION .or. abs(RealConst(5)) > small) then
      active_flag = RealConst(5)
    end if
  end if
  if (active_flag < 0.0d0) then
    ! 自动激活模式用于避免在静力预载与瞬态分析之间调用 RMODIF。
    ! 设置 RealConst(5) = -t_preload_end；例如 -1.0d-3 会让速度阻尼器
    ! 在 TIME=0.001 之前保持关闭，并在后续瞬态求解时间自动激活。
    if (timval <= abs(active_flag) + 1.0d-12) return
  else if (active_flag <= 0.5d0) then
    return
  end if

  fmax = RealConst(1)
  vcr = RealConst(2)
  fc = RealConst(1)
  vs = RealConst(2)
  damping_c = RealConst(1)
  alpha = RealConst(2)
  rel_disp_vec(1:3) = 0.0d0
  rel_vel_vec(1:3) = 0.0d0
  ceff_vec(1:3) = 0.0d0
  force_vec(1:3) = 0.0d0
  dfdv_vec(1:3) = 0.0d0
  tangent_vec(1:3) = 0.0d0

  do comp = 1, 3
    i_node1 = comp
    i_node2 = comp + 3
    rel_disp_vec(comp) = TotValDofs(i_node2) - TotValDofs(i_node1)
    rel_vel_vec(comp) = VelValDofs(i_node2) - VelValDofs(i_node1)

    if (active_direction /= 0 .and. comp /= active_direction) cycle

    rel_vel = rel_vel_vec(comp)
    abs_v = abs(rel_vel)
    vel_sq = rel_vel * rel_vel

    select case (damper_type)
    case (DAMPER_FRICTION)
      tanh_arg = rel_vel / vs
      tanh_val = tanh(tanh_arg)
      force_vec(comp) = fc * tanh_val
      dfdv_vec(comp) = fc / vs * (1.0d0 - tanh_val * tanh_val)
      if (abs_v <= small) then
        ceff_vec(comp) = fc / vs
      else
        ceff_vec(comp) = force_vec(comp) / rel_vel
      end if
    case (DAMPER_VISCOUS)
      if (tangent_mode == TANGENT_COMBIN37_EQUIV) then
        ! 与 COMBIN37 的控制速度 DAMP 修正保持一致：
        ! Ceff = C*|v|^(alpha-1)，Force = Ceff*v，阻尼矩阵 = Ceff。
        vreg = max(abs_v, v_floor)
        if (vreg <= small) then
          force_vec(comp) = 0.0d0
          dfdv_vec(comp) = 0.0d0
          ceff_vec(comp) = 0.0d0
        else
          ceff_vec(comp) = damping_c * vreg**(alpha - 1.0d0)
          force_vec(comp) = ceff_vec(comp) * rel_vel
          if (abs_v < max(v_floor, small)) then
            dfdv_vec(comp) = ceff_vec(comp)
          else
            dfdv_vec(comp) = damping_c * alpha * abs_v**(alpha - 1.0d0)
          end if
        end if
      else if (abs_v <= small) then
        force_vec(comp) = 0.0d0
        dfdv_vec(comp) = 0.0d0
        ceff_vec(comp) = 0.0d0
      else
        force_vec(comp) = damping_c * sign(1.0d0, rel_vel) * abs_v**alpha
        dfdv_vec(comp) = damping_c * alpha * abs_v**(alpha - 1.0d0)
        ceff_vec(comp) = force_vec(comp) / rel_vel
      end if
    case default
      ceff_vec(comp) = 2.0d0 * fmax * vcr / (vel_sq + vcr * vcr)
      force_vec(comp) = ceff_vec(comp) * rel_vel
      dfdv_vec(comp) = 2.0d0 * fmax * vcr * (vcr * vcr - vel_sq) / &
                       (vel_sq + vcr * vcr)**2
    end select

    select case (tangent_mode)
    case (TANGENT_RAW)
      if (damper_type == DAMPER_FRICTION) then
        tangent_vec(comp) = ceff_vec(comp)
      else
        tangent_vec(comp) = dfdv_vec(comp)
      end if
    case (TANGENT_SECANT)
      tangent_vec(comp) = ceff_vec(comp)
    case (TANGENT_FORCE_ONLY)
      tangent_vec(comp) = 0.0d0
    case (TANGENT_DISABLED)
      tangent_vec(comp) = 0.0d0
    case (TANGENT_COMBIN37_EQUIV)
      tangent_vec(comp) = ceff_vec(comp)
    case default
      if (damper_type == DAMPER_FRICTION) then
        tangent_vec(comp) = ceff_vec(comp)
      else
        tangent_vec(comp) = max(dfdv_vec(comp), 0.0d0)
      end if
    end select
  end do

  if (keyMtx(3) == 1) then
    do comp = 1, 3
      if (active_direction /= 0 .and. comp /= active_direction) cycle
      if (tangent_mode /= TANGENT_FORCE_ONLY .and. tangent_mode /= TANGENT_DISABLED) then
        i_node1 = comp
        i_node2 = comp + 3
        eDamp(i_node1, i_node1) = tangent_vec(comp)
        eDamp(i_node1, i_node2) = -tangent_vec(comp)
        eDamp(i_node2, i_node1) = -tangent_vec(comp)
        eDamp(i_node2, i_node2) = tangent_vec(comp)
      end if
    end do
  end if

  if (keyMtx(6) == 1 .and. tangent_mode /= TANGENT_DISABLED .and. &
      .not. (damper_type == DAMPER_VISCOUS .and. &
              tangent_mode == TANGENT_COMBIN37_EQUIV) .and. &
      .not. (damper_type == DAMPER_EDDY .and. &
              tangent_mode /= TANGENT_FORCE_ONLY) .and. &
      .not. (damper_type == DAMPER_FRICTION .and. &
              tangent_mode /= TANGENT_FORCE_ONLY)) then
    do comp = 1, 3
      if (active_direction /= 0 .and. comp /= active_direction) cycle
      i_node1 = comp
      i_node2 = comp + 3
      fInt(i_node1) = -force_vec(comp)
      fInt(i_node2) = force_vec(comp)
    end do
  end if

  result_comp = max(active_direction, 1)

  if (nSaveVars >= 6) then
    saveVars(1) = rel_disp_vec(result_comp)
    saveVars(2) = rel_vel_vec(result_comp)
    saveVars(3) = ceff_vec(result_comp)
    saveVars(4) = force_vec(result_comp)
    saveVars(5) = dfdv_vec(result_comp)
    saveVars(6) = tangent_vec(result_comp)
  else if (nSaveVars >= 5) then
    saveVars(1) = rel_disp_vec(result_comp)
    saveVars(2) = rel_vel_vec(result_comp)
    saveVars(3) = ceff_vec(result_comp)
    saveVars(4) = force_vec(result_comp)
    saveVars(5) = dfdv_vec(result_comp)
  end if

  if (nRsltVar >= 6) then
    RsltVar(1) = rel_disp_vec(result_comp)
    RsltVar(2) = rel_vel_vec(result_comp)
    RsltVar(3) = ceff_vec(result_comp)
    RsltVar(4) = force_vec(result_comp)
    RsltVar(5) = dfdv_vec(result_comp)
    RsltVar(6) = tangent_vec(result_comp)
  else if (nRsltVar >= 5) then
    RsltVar(1) = rel_disp_vec(result_comp)
    RsltVar(2) = rel_vel_vec(result_comp)
    RsltVar(3) = ceff_vec(result_comp)
    RsltVar(4) = force_vec(result_comp)
    RsltVar(5) = dfdv_vec(result_comp)
  end if

end subroutine USERELEM
