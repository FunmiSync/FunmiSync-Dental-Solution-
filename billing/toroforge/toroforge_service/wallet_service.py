from __future__ import annotations
import logging
import secrets
import string
from uuid import UUID
from sqlalchemy.orm import Session, load_only
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from auth.security import encrypt_secret
from core.models import Wallet, WalletStatus, WalletType, RegisteredClinics, Dso
from billing.toroforge.exceptions import (
    ToroForgeDuplicateNameError,
    ToroForgeWalletCreationError,
    ToroForgeValidationError
)
from billing.toroforge.toroforge_client.keystore_client import ToroForgeKeyStoreClient
from billing.toroforge.toroforge_client.tns_client import ToroForgeTNSClient
from billing.toroforge.types import ToroForgeProvisionWalletResult


logger = logging.getLogger(__name__)

class ToroForgeWalletService:
    def __init__(
            self,
            db:Session,
            keystore_client : ToroForgeKeyStoreClient,
            tns_client: ToroForgeTNSClient
    ) -> None:
        
        self.db = db
        self.keystore_client = keystore_client
        self.tns_client = tns_client

    async def create_and_provision_clinic_wallet(
            self,
            *, 
            clinic_id: UUID,
            username: str 
    ) -> ToroForgeProvisionWalletResult:

        logger.info(
            "ToroForge clinic wallet provisioning requested",
            extra={
                "clinic_id": str(clinic_id),
                "username": username,
                "wallet_type": WalletType.CLINIC.value,
            },
        )
        
        
        clinic = (
                self.db.query(RegisteredClinics)
                .options(load_only(RegisteredClinics.id, RegisteredClinics.dso_id))
                .filter(RegisteredClinics.id == clinic_id)
                .first()
            )


        if not clinic:
            logger.warning(
                "ToroForge clinic wallet provisioning failed: clinic not found",
                extra={
                    "clinic_id": str(clinic_id),
                    "username": username,
                    "wallet_type": WalletType.CLINIC.value,
                },
            )
            raise ToroForgeWalletCreationError("Clinic not found ")

        return await self.create_and_provision_wallet(
            username= username,
            wallet_type=WalletType.CLINIC,
            clinic_id = clinic_id,
            dso_id= clinic.dso_id 
            )
    


    async def create_and_provision_dso_wallet(
        self,
        *,
        dso_id: UUID,
        username:str
         )-> ToroForgeProvisionWalletResult:
        
        logger.info(
            "ToroForge DSO wallet provisioning requested",
            extra={
                "dso_id": str(dso_id),
                "username": username,
                "wallet_type": WalletType.DSO_TREASURY.value,
            },
        )

        dso= (self.db.query(Dso.id).filter(Dso.id == dso_id).first())

        if not dso:
            logger.warning(
                "ToroForge DSO wallet provisioning failed: dso not found",
                extra={
                    "dso_id": str(dso_id),
                    "username": username,
                    "wallet_type": WalletType.DSO_TREASURY.value,
                },
            )
            raise ToroForgeWalletCreationError("DSO not found")
        
        return await self.create_and_provision_wallet(
            username=username,
            wallet_type=WalletType.DSO_TREASURY,
            clinic_id=None,
            dso_id=dso_id,
        )
    

    async def create_and_provision_wallet(
            self,
            *,
            username: str,
            wallet_type: WalletType,
            clinic_id: UUID | None,
            dso_id: UUID | None,
    ) -> ToroForgeProvisionWalletResult:
        
        normalized_username = self.normalize_username(username)
        log_ctx = {
            "wallet_type": wallet_type.value,
            "clinic_id": str(clinic_id) if clinic_id else None,
            "dso_id": str(dso_id) if dso_id else None,
            "username": normalized_username,
        }

        logger.info(
            "ToroForge wallet provisioning started",
            extra=log_ctx,
        )

        wallet_query = self.db.query(Wallet).filter(Wallet.wallet_type == wallet_type)

        if wallet_type == WalletType.CLINIC:
            wallet_query = wallet_query.filter(Wallet.clinic_id == clinic_id)
        elif wallet_type == WalletType.DSO_TREASURY:
            wallet_query = wallet_query.filter(Wallet.dso_id == dso_id)
        else:
            logger.error(
                "ToroForge wallet provisioning failed: unsupported wallet type",
                extra=log_ctx,
            )
            raise ToroForgeWalletCreationError(f"Unsupported wallet type: {wallet_type}")
        
        wallet =wallet_query.first()

        if wallet and wallet.status == WalletStatus.ACTIVE and wallet.external_wallet_address:
            logger.warning(
                "ToroForge wallet provisioning blocked: active wallet already exists",
                extra={**log_ctx, "wallet_id": str(wallet.id)},
            )
            raise ToroForgeWalletCreationError("Wallet already exists")
        
        if wallet and wallet.external_wallet_address:
            logger.warning(
                "ToroForge wallet provisioning blocked: partial external state detected",
                extra={
                    **log_ctx,
                    "wallet_id": str(wallet.id),
                    "external_wallet_address": wallet.external_wallet_address,
                },
            )
            raise ToroForgeWalletCreationError(
                "Wallet has partial external state. Reconcile before retrying."
            )
        
        if not wallet:
            wallet = Wallet(
                clinic_id= clinic_id,
                dso_id= dso_id,
                wallet_type = wallet_type,
                status = WalletStatus.PENDING,
                currency = "USD",
                cached_balance_minor = 0,
                auto_debit_enabled = True 
            )
            self.db.add(wallet)
            self.commit_wallet(
                wallet,
                action= "creating local pending row",
                log_ctx = log_ctx,
                duplicate_error_message= "Wallet already exists"
            )
            logger.info(
                "ToroForge local wallet row created",
                extra={**log_ctx, "wallet_id": str(wallet.id)}
            )
        else:
            wallet.status = WalletStatus.PENDING
            wallet.failure_reason = None
            self.commit_wallet(
                wallet,
                action= "resetting existing wallet row to pending",
                log_ctx={**log_ctx, "wallet_id": str(wallet.id)}
            )
            logger.info(
                "ToroForge existing wallet row reset to pending",
                extra={**log_ctx, "wallet_id": str(wallet.id)},
            )

        generated_password = self.generate_wallet_password()
        encrypted_password = encrypt_secret(generated_password)
        external_address: str | None = None

        try:
            logger.info(
                "ToroForge TNS availability check started",
                extra={**log_ctx, "wallet_id": str(wallet.id)},
            )
            await self.tns_client.assert_name_available(username = normalized_username)

            logger.info(
                "ToroForge keystore creation started",
                extra={**log_ctx, "wallet_id": str(wallet.id)},
            )

            external_address = await self.keystore_client.create_keystore(password= generated_password)

            logger.info(
                "ToroForge keystore created",
                extra={
                    **log_ctx,
                    "wallet_id": str(wallet.id),
                    "external_wallet_address": external_address,
                },
            )

            logger.info(
                "ToroForge TNS setname started",
                extra={
                    **log_ctx,
                    "wallet_id": str(wallet.id),
                    "external_wallet_address": external_address,
                },
            )

            await self.tns_client.set_name(
                address=external_address,
                password=generated_password,
                username=normalized_username,
            )

            logger.info(
                "ToroForge TNS setname completed",
                extra={
                    **log_ctx,
                    "wallet_id": str(wallet.id),
                    "external_wallet_address": external_address,
                    "tns username": username
                },
            )

            logger.info(
                "ToroForge wallet verification started",
                extra={
                    **log_ctx,
                    "wallet_id": str(wallet.id),
                    "external_wallet_address": external_address,
                },
            )


            verified = await self.keystore_client.verify_key(
                address= external_address,
                password= generated_password
            )

            if not verified:
                logger.error(
                    "ToroForge wallet verification failed after creation",
                    extra={
                        **log_ctx,
                        "wallet_id": str(wallet.id),
                        "external_wallet_address": external_address,
                    },
                )
                raise ToroForgeWalletCreationError(
                    "ToroForge wallet verification failed after creation"
                )
            logger.info(
                "ToroForge wallet verification passed",
                extra={
                    **log_ctx,
                    "wallet_id": str(wallet.id),
                    "external_wallet_address": external_address,
                },
            )
        except ToroForgeDuplicateNameError:
            self.mark_wallet_failed(
                wallet,
                reason = "ToroForge username is already taken",
                external_address = external_address,
                encrypted_password = encrypted_password if external_address else None,
                log_ctx = log_ctx
            )
            logger.warning(
                "ToroForge wallet provisioning failed: username already taken",
                extra={**log_ctx, "wallet_id": str(wallet.id)},
            )
            raise

        except ToroForgeValidationError as exc:
            self.mark_wallet_failed(
                wallet,
                reason=str(exc),
                external_address=external_address,
                encrypted_password=encrypted_password if external_address else None,
                log_ctx=log_ctx,
            )
            logger.warning(
                "ToroForge wallet provisioning failed due to validation error",
                extra={
                    **log_ctx,
                    "wallet_id": str(wallet.id),
                    "external_wallet_address": external_address,
                },
            )
            raise

        except Exception as exc :
            self.mark_wallet_failed(
                wallet,
                reason=str(exc),
                external_address=external_address,
                encrypted_password=encrypted_password if external_address else None,
                log_ctx=log_ctx,
            )
            logger.exception(
                "ToroForge wallet provisioning failed",
                extra={
                    **log_ctx,
                    "wallet_id": str(wallet.id),
                    "external_wallet_address": external_address,
                },
            )
            raise

        wallet.external_wallet_address = external_address
        wallet.external_wallet_username = normalized_username
        wallet.external_wallet_password_encrypted = encrypted_password
        wallet.status = WalletStatus.ACTIVE
        wallet.failure_reason = None


        self.commit_wallet(
            wallet,
            action="saving successful ToroForge wallet provisioning",
            log_ctx={
                **log_ctx,
                "wallet_id": str(wallet.id),
                "external_wallet_address": external_address,
            },
        )

        logger.info(
            "ToroForge wallet provisioning completed successfully",
            extra={
                **log_ctx,
                "wallet_id": str(wallet.id),
                "external_wallet_address": wallet.external_wallet_address,
            },
        )

        if wallet.external_wallet_address is None:
            raise ToroForgeWalletCreationError(
                "ToroForge wallet provisioning completed without an external wallet address"
            )

        if wallet.external_wallet_username is None:
            raise ToroForgeWalletCreationError(
                "ToroForge wallet provisioning completed without an external wallet username"
            )

        external_wallet_address = wallet.external_wallet_address
        external_wallet_username = wallet.external_wallet_username

        return ToroForgeProvisionWalletResult(
             wallet_id=wallet.id,
             external_wallet_address= external_wallet_address, # type: ignore
             external_wallet_username= external_wallet_username, # type: ignore
            generated_password= generated_password,
        )
    
    def mark_wallet_failed(
            self,
            wallet: Wallet,
            *,
            reason: str,
            external_address: str | None,
            encrypted_password: str | None,
            log_ctx: dict
    ) -> None:
        
        wallet.status = WalletStatus.FAILED
        wallet.failure_reason = reason

        if external_address:
            wallet.external_wallet_address = external_address

        if encrypted_password:
            wallet.external_wallet_password_encrypted = encrypted_password

        self.commit_wallet(
            wallet,
            action="persisting failed ToroForge wallet state",
            log_ctx={**log_ctx, "wallet_id": str(wallet.id), "external_wallet_address": external_address},
        )



    def commit_wallet(
            self,
            wallet: Wallet,
            *,
            action: str,
            log_ctx: dict,
            duplicate_error_message: str | None = None
    ) -> None:
        try:
            self.db.commit()
            self.db.refresh(wallet)
        except IntegrityError as exc:
            self.db.rollback()
            logger.exception(
                "ToroForge wallet database integrity error",
                extra={**log_ctx, "action": action},
            )
            if duplicate_error_message:
                raise ToroForgeWalletCreationError(duplicate_error_message) from exc
            raise ToroForgeWalletCreationError(
                f"Database integrity error while {action}"
            ) from exc
        
        except SQLAlchemyError as exc:
            self.db.rollback()
            logger.exception(
                "ToroForge wallet database commit failed",
                extra={**log_ctx, "action": action},
            )
            raise ToroForgeWalletCreationError(
                f"Database error while {action}"
            ) from exc
        


    def normalize_username(
            self,
            username: str 
    ) -> str:
        
        normalized = username.strip().lower()
        if not normalized:
            logger.warning("ToroForge username validation failed: empty username")
            raise ToroForgeWalletCreationError("Wallet username is required")
        return normalized
    
    
    def generate_wallet_password(
            self,
            *, 
            length: int = 32,   
    ) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    


        


    





            













    


        


        


